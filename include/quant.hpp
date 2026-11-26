#ifndef QUANT_QUANT_HPP_
#define QUANT_QUANT_HPP_

// QUANT: deterministic financial core and provider-neutral domain models.
// All monetary values are integer paise. All time is epoch milliseconds unless
// a field is explicitly named *_date or ISO.

#include <algorithm>
#include <array>
#include <compare>
#include <cstdint>
#include <cstdlib>
#include <deque>
#include <functional>
#include <map>
#include <limits>
#include <memory>
#include <optional>
#include <set>
#include <string>
#include <string_view>
#include <thread>
#include <unordered_map>
#include <utility>
#include <vector>

namespace quant {

class Journal;
struct Event;

// ---------------------------------------------------------------------------
// Money: integer paise. Conversions are explicit to prevent unit confusion.
// ---------------------------------------------------------------------------
namespace money_detail {

constexpr bool CheckedAdd(int64_t left, int64_t right, int64_t* out) {
  if (out == nullptr)
    return false;
  if ((right > 0 && left > std::numeric_limits<int64_t>::max() - right) ||
      (right < 0 && left < std::numeric_limits<int64_t>::min() - right)) {
    return false;
  }
  *out = left + right;
  return true;
}

// Dedicated subtraction: never evaluates -right when right == INT64_MIN.
constexpr bool CheckedSubtract(int64_t left, int64_t right, int64_t* out) {
  if (out == nullptr)
    return false;
  if (right > 0) {
    if (left < std::numeric_limits<int64_t>::min() + right)
      return false;
  } else if (right < 0) {
    if (left > std::numeric_limits<int64_t>::max() + right)
      return false;
  }
  *out = left - right;
  return true;
}

constexpr bool CheckedMultiply(int64_t left, int64_t right, int64_t* out) {
  if (out == nullptr)
    return false;
  if (left == 0 || right == 0) {
    *out = 0;
    return true;
  }
#if defined(__SIZEOF_INT128__)
  const __int128 product = static_cast<__int128>(left) * right;
  if (product > std::numeric_limits<int64_t>::max() ||
      product < std::numeric_limits<int64_t>::min()) {
    return false;
  }
  *out = static_cast<int64_t>(product);
  return true;
#else
  if (left == -1 || right == -1) {
    if (left == std::numeric_limits<int64_t>::min() ||
        right == std::numeric_limits<int64_t>::min()) {
      return false;
    }
  }
  if (left > 0) {
    if (right > 0 && left > std::numeric_limits<int64_t>::max() / right)
      return false;
    if (right < 0 && right < std::numeric_limits<int64_t>::min() / left)
      return false;
  } else {
    if (right > 0 && left < std::numeric_limits<int64_t>::min() / right)
      return false;
    if (right < 0 && left < std::numeric_limits<int64_t>::max() / right)
      return false;
  }
  *out = left * right;
  return true;
#endif
}

}  // namespace money_detail

// Money is an optional monetary value expressed in integer paise.
//
// Contract:
//   * Invalid money is an explicit absence of a value, never a zero amount.
//   * Ordering with an invalid operand is UNORDERED: every relational operator
//     (<, <=, >, >=) and equality returns false. Invalid values cannot approve
//     a comparison in a financial decision.
//   * TryPaise/TryRupees are the checked extraction API and return false for an
//     invalid value. They are the only conversions that may be used when the
//     validity of a value is not already established.
//   * paise()/rupees() are precondition accessors: they require valid() == true
//     and terminate on violation. A violation is a hard, observable failure,
//     never a silent conversion of invalid money to zero.
//   * All arithmetic propagates invalidity and reports overflow as invalid.
class Money {
 public:
  constexpr Money() : paise_(0), valid_(true) {
  }
  static constexpr Money FromPaise(int64_t paise) {
    return Money(paise, true);
  }
  static Money FromRupees(double rupees);
  static bool TryFromRupees(double rupees, Money* out);
  static constexpr Money Invalid() {
    return Money(0, false);
  }
  constexpr bool valid() const {
    return valid_;
  }

  bool TryPaise(int64_t* out) const {
    if (out == nullptr || !valid_)
      return false;
    *out = paise_;
    return true;
  }
  bool TryRupees(double* out) const {
    if (out == nullptr || !valid_)
      return false;
    *out = static_cast<double>(paise_) / 100.0;
    return true;
  }

  // Precondition accessors. See the class contract above.
  constexpr int64_t paise() const {
    if (!valid_)
      std::abort();
    return paise_;
  }
  constexpr double rupees() const {
    if (!valid_)
      std::abort();
    return static_cast<double>(paise_) / 100.0;
  }

  constexpr Money& operator+=(const Money& other) {
    int64_t result = 0;
    if (!valid_ || !other.valid_ ||
        !money_detail::CheckedAdd(paise_, other.paise_, &result)) {
      paise_ = 0;
      valid_ = false;
      return *this;
    }
    paise_ = result;
    return *this;
  }
  constexpr Money& operator-=(const Money& other) {
    int64_t result = 0;
    if (!valid_ || !other.valid_ ||
        !money_detail::CheckedSubtract(paise_, other.paise_, &result)) {
      paise_ = 0;
      valid_ = false;
      return *this;
    }
    paise_ = result;
    return *this;
  }

 private:
  constexpr Money(int64_t paise, bool valid) : paise_(paise), valid_(valid) {
  }
  int64_t paise_;
  bool valid_;
};

constexpr Money operator+(Money a, Money b) {
  return a += b;
}
constexpr Money operator-(Money a, Money b) {
  return a -= b;
}

// Invalid operands are UNORDERED. All derived relational operators therefore
// return false, so an invalid amount can never satisfy a financial guard.
constexpr std::partial_ordering operator<=>(Money a, Money b) {
  if (!a.valid() || !b.valid())
    return std::partial_ordering::unordered;
  if (a.paise() < b.paise())
    return std::partial_ordering::less;
  if (a.paise() > b.paise())
    return std::partial_ordering::greater;
  return std::partial_ordering::equivalent;
}
constexpr bool operator==(Money a, Money b) {
  return a.valid() && b.valid() && a.paise() == b.paise();
}

bool IsCanonicalExchangeDate(std::string_view text);

// ---------------------------------------------------------------------------
// Enumerations and configuration.
// ---------------------------------------------------------------------------
enum class Mode {
  kDisabled,
  kBacktest,
  kPaper,
  kReadOnlyBroker,
  kLive,
};

enum class Provider { kFyers, kUpstox, kZerodha, kSynthetic };

enum class EventType {
  kQuote,
  kCandle,
  kSignal,
  kRiskDecision,
  kOrder,
  kFill,
  kAccounting,
  kPaperIntent,
  kHeartbeat,
};

enum class OrderState {
  kNew,
  kValidated,
  kSent,
  kAcknowledged,
  kPartiallyFilled,
  kFilled,
  kRejected,
  kCancelled,
  kAmbiguous,
};

enum class OrderSide { kBuy, kSell };

struct KdbxConfig {
  bool enabled = true;
  std::string host = "127.0.0.1";  // Loopback only; never exposed publicly.
  int port = 5001;
  int timeout_ms = 2000;
  std::string username = "quant";
  std::string script = "q/quant.q";
  std::string hdb_dir = "data/hdb";
  std::string log_path = "data/db/q.log";
  std::string pid_path = "data/db/q.pid";
  size_t batch_size = 512;
};

struct KdbxStatus {
  bool compiled = false;
  bool runtime = false;
  bool license_present = false;
  bool pykx_present = false;
  bool connected = false;
  std::string detail;
};

struct Config {
  Mode mode = Mode::kDisabled;
  bool live_trading_enabled = false;
  bool allow_margin = false;
  bool allow_short_selling = false;
  bool allow_withdrawals = false;
  Money initial_capital = Money::FromRupees(500.0);
  double risk_per_trade_fraction = 0.01;
  double max_daily_loss_fraction = 0.02;
  double max_drawdown_fraction = 0.10;
  int max_open_positions = 1;
  double max_gross_exposure_fraction = 0.80;
  double cash_reserve_fraction = 0.10;
  double max_spread_bps = 35.0;
  double max_participation_fraction = 0.01;
  double stop_loss_fraction = 0.05;
  bool backtest_delivery = true;
  bool execution_delivery = true;
  bool terminal_liquidation = false;
  double min_traded_value_inr = 5e6;
  double slippage_bps = 10.0;
  size_t history_days = 260;
  size_t universe_limit = 60;
  size_t shortlist_size = 8;
  size_t queue_capacity = 4096;
  int stale_quote_seconds = 300;
  int http_timeout_seconds = 30;
  std::string keychain_prefix = "NIRNAY_";
  std::string data_dir = "data";
  std::string journal_path = "data/journal/events.jnl";
  std::string risk_snapshot_path = "data/journal/risk.snap";
  std::string account_id = "PRIMARY";
  std::string universe_path = "data/cache/universe.csv";
  std::string history_dir = "data/cache/history";
  std::string history_raw_dir = "data/cache/raw";
  std::string report_dir = "data/reports";
  std::vector<std::string> seed_symbols;
  std::vector<std::string> index_symbols;
  KdbxConfig kdbx;
};

struct ConfigResult {
  Config config;
  std::vector<std::string> errors;
  bool ok() const {
    return errors.empty();
  }
};

ConfigResult LoadConfig(const std::string& path);
std::vector<std::string> ValidateConfig(const Config& config);
std::string_view ModeName(Mode mode);
Mode ParseMode(std::string_view text);

// ---------------------------------------------------------------------------
// Canonical domain models.
// ---------------------------------------------------------------------------
enum class AssetClass {
  kCashEquity,
  kEtf,
  kDerivative,
  kFx,
  kRates,
  kCommodity,
};

struct InstrumentSpec {
  AssetClass asset_class = AssetClass::kCashEquity;
  std::string exchange;
  std::string calendar_id;
  std::string fee_schedule_id;
  double tick_size = 0.0;
  int lot_size = 0;
  bool venue_supported = false;
};
bool ValidateInstrumentSpec(const InstrumentSpec& spec, std::string* error);

enum class ExecutionProfile { kDaily, kIntraday, kHft };
bool ExecutionProfileSupported(ExecutionProfile profile);

struct ComplianceGateState {
  bool written_broker_approval = false;
  bool compliance_signoff = false;
  bool operational_risk_signoff = false;
  bool sandbox_certified = false;
  std::string approval_ref;
};
bool LiveRouteAuthorized(const ComplianceGateState& gate, std::string* error);

struct ReferenceInstrument {
  uint64_t schema_version = 1;
  std::string permanent_id;
  std::string venue;
  std::string isin;
  AssetClass asset_class = AssetClass::kCashEquity;
  std::string currency;
  int price_scale = 0;
  int quantity_scale = 0;
  int64_t tick_size_minor = 0;
  int64_t lot_size = 0;
  int64_t contract_multiplier = 0;
  std::string calendar_id;
  std::string session_model;
  std::string settlement_model;
  std::string margin_model;
  std::string fee_schedule_id;
  int64_t effective_from_ms = 0;
  int64_t effective_to_ms = 0;
};
bool ValidateReferenceInstrument(const ReferenceInstrument& instrument,
                                 std::string* error);
bool VenueConformanceSupported(const std::string& venue,
                               AssetClass asset_class,
                               const std::string& calendar_id);

enum class InstrumentClass {
  kUnknown,
  kEquity,
  kIndex,
  kEtf,
  kFund,
  kDerivative,
  kSme,
  kOther,
};

struct Instrument {
  std::string symbol;
  std::string exchange = "NSE";
  std::string isin;
  std::string
      provider_key;  // Upstox instrument_key, canonical identity anchor.
  std::string name;
  std::string security_type;
  double tick_size = 0.05;
  double freeze_quantity = 0.0;
  bool cas_eligible = false;
  InstrumentClass instrument_class = InstrumentClass::kUnknown;
  bool eligible = false;
  std::string eligibility_reason;
};

struct Quote {
  std::string symbol;
  std::string provider_key;
  Provider source = Provider::kSynthetic;
  double last = 0.0;
  double bid = 0.0;
  double ask = 0.0;
  int64_t bid_qty = 0;
  int64_t ask_qty = 0;
  double volume = 0.0;
  double open = 0.0;
  double high = 0.0;
  double low = 0.0;
  double close = 0.0;
  int64_t exchange_timestamp_ms = 0;    // Last exchange trade event time.
  int64_t provider_timestamp_ms = 0;    // Provider feed update time.
  int64_t last_trade_timestamp_ms = 0;  // Last trade time, kept distinct.
  int64_t receive_timestamp_ms =
      0;  // steady monotonic clock, never used as market time.
  uint64_t sequence = 0;
  std::string quality;  // ok | stale | missing_depth | cross_provider_mismatch
};

struct Candle {
  std::string date;  // YYYY-MM-DD, session date IST.
  double open = 0.0;
  double high = 0.0;
  double low = 0.0;
  double close = 0.0;
  double volume = 0.0;
  int64_t timestamp_ms = 0;
};

struct FeatureSet {
  double ret5 = 0.0;
  double ret20 = 0.0;
  double ret60 = 0.0;
  double ret120 = 0.0;
  double vol20 = 0.0;
  double volume_surprise = 0.0;
  double trend = 0.0;     // 0..1 fraction of bullish conditions.
  double breakout = 0.0;  // close vs prior 20-day range, -1..1.
  double vwap_dev = 0.0;
  bool valid = false;
};

struct Signal {
  std::string symbol;
  std::string strategy;
  double score = 0.0;
  double confidence = 0.0;
  std::string rationale;
  FeatureSet features;
  bool tradable = false;
  std::string rejection_reason;
};

struct OrderRequest {
  std::string symbol;
  std::string provider_key;
  OrderSide side = OrderSide::kBuy;
  int quantity = 0;
  double limit_price = 0.0;
  std::string client_order_id;
};

struct OrderResult {
  bool accepted = false;
  bool simulated = false;
  std::string broker_order_id;
  std::string status;
  std::string message;
};

struct Position {
  std::string symbol;
  int quantity = 0;
  Money avg_price;
  Money last_price;
  int64_t opened_at_ms = 0;
};

struct Ledger {
  Money initial_capital;
  Money cash;
  Money invested;
  Money realized_pnl;
  Money unrealized_pnl;
  Money brokerage;
  Money statutory;
  Money slippage;
  Money operating;
  Money estimated_tax;
  Money peak_equity;
  Money external_flows;  // Must remain zero; non-zero is a reconciliation
                         // exception.
  int trade_count = 0;
};

// Every financial calculation reports how it was produced. A default
// CostBreakdown is NOT a valid zero-cost result: it is an invalid input and
// must be rejected by the portfolio.
enum class CalculationStatus {
  kCalculated,    // exact, fully implemented
  kEstimated,     // implemented, but contains an explicitly estimated input
  kUnsupported,   // required behaviour is not implemented
  kInvalidInput,  // an operand was invalid
  kOverflow,      // checked arithmetic overflowed
};
const char* CalculationStatusName(CalculationStatus status);

struct CostBreakdown {
  Money brokerage;
  Money stt;
  Money exchange_fee;
  Money gst;
  Money sebi;
  Money stamp;
  Money dp;
  Money slippage;
  Money total;
  CalculationStatus status = CalculationStatus::kInvalidInput;
  std::string version =
      "ZERODHA-EQ-2026-11-25-v1 (DP settlement aggregation NOT IMPLEMENTED)";

  bool usable() const {
    return (status == CalculationStatus::kCalculated ||
            status == CalculationStatus::kEstimated) &&
           brokerage.valid() && stt.valid() && exchange_fee.valid() &&
           gst.valid() && sebi.valid() && stamp.valid() && dp.valid() &&
           slippage.valid() && total.valid();
  }
};

// ---------------------------------------------------------------------------
// Transaction costs. C++ is authoritative for all financial arithmetic.
// ---------------------------------------------------------------------------
CostBreakdown EstimateCosts(Money turnover,
                            bool delivery,
                            bool is_buy,
                            double slippage_bps,
                            bool charge_dp = true);

class CostAggregator {
 public:
  CostBreakdown Estimate(Money turnover,
                         bool delivery,
                         bool is_buy,
                         double slippage_bps,
                         const std::string& symbol,
                         const std::string& session);

  // Records a committed execution's DP entitlement. Estimation never mutates
  // this state; only an actual or simulated recognised execution may do so.
  // dp_applicable reports whether this execution is the first qualifying
  // delivery sell for the scrip/session.
  bool RecognizeExecution(const std::string& symbol,
                          const std::string& session,
                          bool delivery,
                          bool is_buy,
                          bool* dp_applicable,
                          std::string* error);
  size_t charged_session_count() const {
    return dp_charged_.size();
  }

 private:
  std::set<std::string> dp_charged_;
};
double BreakEvenMoveBps(Money notional,
                        const CostBreakdown& entry,
                        const CostBreakdown& exit_costs);
std::string FormatCosts(const CostBreakdown& costs);

// ---------------------------------------------------------------------------
// Portfolio accounting.
// ---------------------------------------------------------------------------
class Portfolio {
 public:
  Portfolio();
  explicit Portfolio(Money initial_capital);
  bool Buy(const std::string& symbol,
           int quantity,
           Money price,
           const CostBreakdown& costs,
           std::string* error,
           const std::string& audit_tag = "");
  bool Sell(const std::string& symbol,
            int quantity,
            Money price,
            const CostBreakdown& costs,
            std::string* error,
            const std::string& audit_tag = "");
  bool MarkToMarket(const std::string& symbol,
                    Money price,
                    std::string* error = nullptr);
  bool RecordOperatingExpense(Money amount, std::string* error = nullptr);
  bool RecordExternalFlow(Money amount,
                          const std::string& reason,
                          std::string* error = nullptr);

  Money equity() const;
  Money available_cash() const;
  Money market_value() const;
  Money drawdown() const;
  double drawdown_fraction() const;
  int open_position_count() const {
    return static_cast<int>(positions_.size());
  }
  const std::map<std::string, Position>& positions() const {
    return positions_;
  }
  Ledger ledger() const {
    return ledger_;
  }
  const std::vector<std::string>& audit() const {
    return audit_;
  }
  bool HasAudit(const std::string& marker) const {
    return std::find(audit_.begin(), audit_.end(), marker) != audit_.end();
  }

 private:
  // One authoritative state-transition guard. A Transaction snapshots the
  // ledger and positions; Commit validates every invariant and is the only
  // path that makes a mutation visible. Destruction without Commit restores
  // the exact prior state, so no failed operation can leave a partial post.
  class Transaction {
   public:
    explicit Transaction(Portfolio* owner);
    ~Transaction();
    bool Commit(std::string* error);
    Transaction(const Transaction&) = delete;
    Transaction& operator=(const Transaction&) = delete;

   private:
    Portfolio* owner_;
    Ledger prior_ledger_;
    std::map<std::string, Position> prior_positions_;
    bool committed_ = false;
  };
  friend class Transaction;

  void UpdatePeakEquity();
  bool RecomputeUnrealized();
  bool ValidateInvariants(std::string* error) const;

  Ledger ledger_;
  std::map<std::string, Position> positions_;
  std::vector<std::string> audit_;
};

// In-memory deterministic event queue prototype. It is NOT a durable event
// backbone: there is no journal, reducer transaction or persisted offset.
// It separates received/committed semantics and uses bounded non-blocking
// overload policies; durability claims are explicitly out of scope.
// Non-blocking overload policies. kRetry means the caller must retry later;
// no thread is blocked or suspended by this runtime.
enum class QueuePolicy { kReject, kRetry };

struct EventEnvelope {
  std::string event_id;
  std::string partition_key;
  uint64_t source_sequence = 0;
  uint64_t replay_offset = 0;
  int64_t event_time_ms = 0;
  int64_t receive_time_ms = 0;
  EventType type = EventType::kHeartbeat;
  std::string payload;
};

struct RuntimeResult {
  size_t received = 0;
  size_t committed = 0;
  size_t rejected = 0;
  size_t duplicates = 0;
  size_t conflicts = 0;
  size_t out_of_order = 0;
  size_t offset_failures = 0;
  bool quarantined = false;
  std::string detail;
};

class EventRuntime {
 public:
  EventRuntime(size_t capacity, QueuePolicy policy);
  bool Submit(const EventEnvelope& event, std::string* error);
  bool ProcessOne(std::string* error);
  RuntimeResult Drain(std::string* error);
  size_t queue_depth() const {
    return queue_.size();
  }
  const std::map<std::string, uint64_t>& offsets() const {
    return offsets_;
  }
  const std::map<std::string, uint64_t>& sequences() const {
    return sequences_;
  }
  const std::map<std::string, uint64_t>& accepted_sequences() const {
    return accepted_;
  }
  bool Abort(const std::string& event_id);
  const std::map<std::string, std::string>& digests() const {
    return digests_;
  }
  const RuntimeResult& result() const {
    return result_;
  }
  size_t applied() const {
    return applied_;
  }

 private:
  std::deque<EventEnvelope> queue_;
  std::map<std::string, uint64_t> offsets_;
  std::map<std::string, uint64_t> sequences_;
  std::map<std::string, uint64_t> accepted_;
  std::map<std::string, std::string> digests_;
  RuntimeResult result_;
  size_t applied_ = 0;
  size_t capacity_;
  QueuePolicy policy_;
};

// ---------------------------------------------------------------------------
// Central risk engine. All strategies must pass through it.
// ---------------------------------------------------------------------------
struct RiskDecision {
  bool approved = false;
  int quantity = 0;
  Money notional;
  std::string reason;
};

struct DailyRiskState {
  std::string account_id = "PRIMARY";
  std::string date;  // Exchange session identity (IST trading day).
  std::string policy_version = "RISK-2026-11-26-v1";
  uint64_t journal_sequence = 0;
  Money starting_equity;
  Money current_equity;
  Money peak_equity;
  Money low_equity;
  Money realized_pnl;
  Money unrealized_pnl;
  bool halted = false;
  std::string halt_reason;
};

// Persistent checkpoint. The journal remains the source of truth; a snapshot
// is only a recovery accelerator with a verified journal watermark.
struct RiskSnapshot {
  uint64_t version = 1;
  std::string account_id;
  std::string session_id;
  std::string policy_version;
  uint64_t journal_sequence = 0;
  int64_t starting_equity_paise = 0;
  int64_t current_equity_paise = 0;
  int64_t peak_equity_paise = 0;
  int64_t low_equity_paise = 0;
  int64_t realized_pnl_paise = 0;
  int64_t unrealized_pnl_paise = 0;
  bool halted = false;
  std::string halt_reason;
  std::string journal_identity;
  std::string watermark_event_checksum;
  std::string checksum;
};

std::string JournalIdentity(const std::vector<Event>& events,
                            uint64_t through_sequence = 0);
bool FillRiskCheckpointIdentity(RiskSnapshot* snapshot,
                                const std::vector<Event>& events,
                                std::string* error);
bool VerifyRiskCheckpointIdentity(const RiskSnapshot& snapshot,
                                  const std::vector<Event>& events,
                                  const std::string& expected_account,
                                  std::string* error);

bool SaveRiskSnapshot(const std::string& path,
                      const RiskSnapshot& snapshot,
                      std::string* error);
bool LoadRiskSnapshot(const std::string& path,
                      RiskSnapshot* snapshot,
                      std::string* error);
std::string RiskSnapshotCanonical(const RiskSnapshot& snapshot);

// Deterministic startup reconstruction. Returns false when the reconstructed
// state is inconsistent; callers must keep execution blocked in that case.
bool RecoverRiskState(const std::string& snapshot_path,
                      const std::string& journal_path,
                      DailyRiskState* state,
                      std::string* error);
bool RecoverRiskState(const std::string& snapshot_path,
                      const std::string& journal_path,
                      DailyRiskState* state,
                      const std::string& expected_account,
                      std::string* error);
bool RecoverRiskStateFromJournal(const std::string& snapshot_path,
                                 Journal* journal,
                                 DailyRiskState* state,
                                 const std::string& expected_account,
                                 std::string* error);

// Durable risk events written before a decision becomes visible.
bool AppendRiskEvent(Journal* journal,
                     const std::string& action,
                     DailyRiskState* state,
                     int64_t now_ms,
                     std::string* error);

struct SizingInput {
  Money price;  // Intended executable price, not LTP.
  OrderSide side = OrderSide::kBuy;
  double volatility = 0.0;      // Daily return standard deviation (fraction).
  double stop_distance = 0.0;   // Explicit stop fraction; volatility if zero.
  int64_t available_depth = 0;  // Side-specific executable depth.
  double tick_size = 0.0;       // Instrument tick size when known.
  double participation = 0.0;   // Optional override of the config cap.
  Money current_exposure;       // Exposure already held by the portfolio.
  bool capacity_estimate = false;  // Historical capacity, not live depth.
  bool delivery = false;           // Charge model used for the cash reserve.
};

struct SizingResult {
  int quantity = 0;
  Money notional;
  std::string reason;
};

// Single authoritative sizing calculation shared by risk, backtest and paper.
// Uses current verified equity, available cash, reserves, drawdown and costs.
SizingResult SizePosition(const Config& config,
                          const Portfolio& portfolio,
                          const SizingInput& input);

class RiskEngine {
 public:
  explicit RiskEngine(Config config);
  explicit RiskEngine(Config config, std::shared_ptr<DailyRiskState> state);
  RiskEngine(Config config,
             std::shared_ptr<DailyRiskState> state,
             Journal* journal);
  RiskDecision Evaluate(const Signal& signal,
                        const Quote& quote,
                        const Portfolio& portfolio,
                        int64_t now_ms);
  const DailyRiskState& daily_state() const {
    return *state_;
  }

 private:
  Config config_;
  std::shared_ptr<DailyRiskState> state_;
  Journal* journal_ = nullptr;
};

// ---------------------------------------------------------------------------
// Strategy features and baselines.
// ---------------------------------------------------------------------------
FeatureSet ComputeFeatures(const std::vector<Candle>& candles);
double CrossSectionalMomentumScore(const FeatureSet& features);
double TimeSeriesMomentumScore(const FeatureSet& features);
double VolumeBreakoutScore(const FeatureSet& features);
double RegimeScore(const std::vector<Candle>& index_candles);
double EnsembleScore(const FeatureSet& features, double regime);

struct BacktestMetrics {
  int trades = 0;
  int wins = 0;
  int open_positions = 0;
  Money gross_pnl;
  Money net_pnl;
  Money realized_pnl;
  Money unrealized_pnl;
  Money brokerage;
  Money statutory;
  Money slippage;
  Money max_drawdown;
  bool terminal_liquidation_requested = false;
  bool terminal_liquidation_completed = false;
  bool terminal_liquidation_failed = false;
  int terminal_open_positions = 0;
  bool halted = false;
  std::string profit_factor_status = "UNDEFINED";
  std::string cost_to_gross_status = "NOT_MEANINGFUL";
  std::vector<std::string> warnings;
  std::vector<std::string> audit;
  double sharpe = 0.0;
  double sortino = 0.0;
  double profit_factor = 0.0;
  double cost_to_gross = 0.0;
  double final_equity = 0.0;
};

// Conservative long-stop fill: if the stop was crossed, assume the adverse
// path occurred before any favourable move. A gap-down fills at the open.
Money ConservativeStopFill(double entry_price,
                           const Candle& candle,
                           double stop_fraction = 0.05);

// First session strictly after the given date, in an instrument's own series.
size_t NextSessionIndex(const std::vector<Candle>& series,
                        const std::string& date);

// Average volume from observations strictly before the given index.
double PriorAverageVolume(const std::vector<Candle>& series,
                          size_t index,
                          size_t window = 20);

BacktestMetrics RunBacktest(
    const std::map<std::string, std::vector<Candle>>& history,
    const Config& config);
std::vector<double> BootstrapSharpeCI(const std::vector<double>& returns,
                                      uint64_t seed,
                                      size_t draws = 1000);
double DeflatedSharpe(double sharpe, size_t trials, size_t observations);

// ---------------------------------------------------------------------------
// Append-only journal with checksums, ids and deterministic replay.
// ---------------------------------------------------------------------------
struct Event {
  uint64_t sequence = 0;
  std::string id;
  EventType type = EventType::kHeartbeat;
  int64_t timestamp_ms = 0;
  int schema_version = 1;
  std::string payload;
  std::string checksum;
};

// ONE canonical accounting posting contract. Every producer and the replay
// reducer use these functions; no producer writes an ad-hoc accounting event.
struct AccountingPosting {
  uint64_t schema_version = 1;
  std::string posting_id;
  std::string order_id;
  std::string action;  // BUY | SELL | EXPENSE | FLOW
  std::string symbol;
  int quantity = 0;
  int64_t price_paise = 0;
  int64_t notional_paise = 0;
  int64_t amount_paise = 0;
  CostBreakdown costs;
};
bool RebuildPortfolioFromJournal(Journal* journal,
                                 Money initial_capital,
                                 Portfolio* out,
                                 std::string* error);
std::string AccountingPayloadDigest(const AccountingPosting& posting);
bool ValidateAccountingPosting(const AccountingPosting& posting,
                               std::string* error);
bool AppendAccountingPosting(Journal* journal,
                             const AccountingPosting& posting,
                             std::string* error);
bool ParseAccountingPosting(const Event& event,
                            AccountingPosting* posting,
                            std::string* error);
bool ApplyAccountingPosting(const AccountingPosting& posting,
                            Portfolio* portfolio,
                            std::string* error);

uint64_t Checksum(std::string_view text);
std::string ChecksumHex(std::string_view text);
std::string EventId(uint64_t sequence, std::string_view payload);

class Journal {
 public:
  bool Open(const std::string& path, std::string* error);
  bool Append(Event* event, std::string* error);
  std::vector<Event> Replay(std::string* error) const;
  uint64_t next_sequence() const {
    return next_sequence_;
  }
  size_t size() const {
    return size_;
  }
  bool healthy() const {
    return healthy_;
  }
  void Close();
  const std::string& path() const {
    return path_;
  }
  ~Journal();

 private:
  std::string path_;
  uint64_t next_sequence_ = 1;
  size_t size_ = 0;
  bool healthy_ = false;
  bool locked_ = false;
  std::thread::id writer_thread_;
  int lock_fd_ = -1;
};

// KDB-X is a materialised analytical projection of the C++ journal. A
// checkpoint records the exact journal watermark it represents; recovery
// replays only events committed after that watermark.
struct KdbxCheckpoint {
  uint64_t version = 1;
  uint64_t journal_watermark = 0;
  uint64_t quote_count = 0;
  uint64_t bar_count = 0;
  uint64_t feature_count = 0;
  bool complete = false;
  std::string runtime_version;
  int64_t created_at_ms = 0;
  std::string checksum;
};
std::string KdbxCheckpointCanonical(const KdbxCheckpoint& checkpoint);
bool SaveKdbxCheckpoint(const std::string& path,
                        const KdbxCheckpoint& checkpoint,
                        std::string* error);
bool LoadKdbxCheckpoint(const std::string& path,
                        KdbxCheckpoint* checkpoint,
                        std::string* error);
bool KdbxRecoveryPlan(const KdbxCheckpoint& checkpoint,
                      const std::vector<Event>& journal,
                      std::vector<Event>* replay,
                      std::string* error);

// Canonical market-data pipeline. Provider adapters produce validated domain
// events; the journal is the durability boundary; a publisher projects those
// committed events into KDB-X. No provider transport is implemented here.
class MarketDataPublisher {
 public:
  virtual ~MarketDataPublisher() = default;
  virtual bool PublishQuote(const Quote& quote,
                            uint64_t sequence,
                            const std::string& event_id,
                            std::string* error) = 0;
  virtual bool PublishBar(const Candle& candle,
                          const std::string& symbol,
                          std::string* error) = 0;
};

enum class PipelineStatus { kComplete, kPartial, kFailed, kBlocked };
const char* PipelineStatusName(PipelineStatus status);

struct PipelineResult {
  PipelineStatus status = PipelineStatus::kBlocked;
  size_t published = 0;
  size_t skipped = 0;
  std::string detail;
};

class MarketDataPipeline {
 public:
  explicit MarketDataPipeline(Journal* journal);
  bool IngestQuote(const Quote& quote, std::string* error);
  bool IngestBar(const Candle& candle,
                 const std::string& symbol,
                 std::string* error);
  PipelineResult Publish(const KdbxCheckpoint& checkpoint,
                         MarketDataPublisher* publisher,
                         std::string* error);

 private:
  Journal* journal_;
};

// Paper execution is a local simulation. It never contacts a broker order
// endpoint and never requires a live trading session.
enum class PaperOrderState {
  kIntent,
  kRiskApproved,
  kSubmitted,
  kAcknowledged,
  kWorking,
  kPartiallyFilled,
  kFilled,
  kRejected,
  kCancelled,
  kExpired,
  kAmbiguous,
};
const char* PaperOrderStateName(PaperOrderState state);

// One versioned fill identity, used by live application and by replay. The
// reducer below is the only place that mutates order fill accumulators.
struct FillRecord {
  int version = 2;
  std::string id;
  std::string order_id;
  std::string symbol;
  int quantity = 0;
  int64_t price_paise = 0;
  int64_t notional_paise = 0;
};
struct PaperOrder {
  std::string id;
  std::string symbol;
  OrderSide side = OrderSide::kBuy;
  int quantity = 0;
  int filled_quantity = 0;
  Money limit_price;
  Money average_fill;
  PaperOrderState state = PaperOrderState::kIntent;
  struct Fill {
    std::string id;
    int quantity = 0;
    int64_t price_paise = 0;
    int64_t notional_paise = 0;
  };
  std::string client_order_id;
  std::string instrument_key;
  std::string order_type = "LIMIT";
  std::string time_in_force = "DAY";
  std::string risk_policy_id;
  std::string config_version;
  std::string execution_model;
  std::string causation_id;
  std::string correlation_id;
  int64_t cumulative_notional_paise = 0;
  std::vector<Fill> fills;
  size_t posted_fill_count = 0;
  std::string reason;
  std::vector<std::string> audit;
};

class PaperBroker {
 public:
  PaperBroker(Config config,
              Journal* journal,
              std::shared_ptr<Portfolio> portfolio);
  PaperOrder* CreateIntent(const std::string& symbol,
                           OrderSide side,
                           int quantity,
                           Money limit_price,
                           std::string* error);
  bool RiskApprove(const std::string& id,
                   const RiskDecision& decision,
                   std::string* error);
  bool Reject(const std::string& id,
              const std::string& reason,
              std::string* error);
  bool Submit(const std::string& id, std::string* error);
  bool Acknowledge(const std::string& id, std::string* error);
  bool ApplyQuote(const std::string& id,
                  const Quote& quote,
                  int64_t now_ms,
                  std::string* error);
  bool Cancel(const std::string& id, std::string* error);
  bool Expire(const std::string& id, std::string* error);
  bool MarkAmbiguous(const std::string& id, std::string* error);
  bool Reconcile(const std::string& id, std::string* error);
  bool RehydrateFromJournal(std::string* error);
  const PaperOrder* Find(const std::string& id) const;
  size_t order_count() const {
    return orders_.size();
  }

 private:
  bool ApplyFillRecord(PaperOrder* order,
                       const FillRecord& fill,
                       std::string* error);
  bool Transition(PaperOrder* order,
                  PaperOrderState state,
                  const std::string& reason,
                  std::string* error,
                  const PaperOrder::Fill* fill = nullptr);
  Config config_;
  Journal* journal_;
  std::shared_ptr<Portfolio> portfolio_;
  std::map<std::string, PaperOrder> orders_;
  std::set<std::string> posted_ids_;
  uint64_t next_id_ = 1;
};

// ---------------------------------------------------------------------------
// Minimal JSON value + parser (no third-party dependency).
// ---------------------------------------------------------------------------
class Json {
 public:
  enum class Type {
    kNull,
    kBool,
    kNumber,
    kInteger,
    kUInteger,
    kString,
    kArray,
    kObject
  };
  using Object = std::map<std::string, Json>;

  Json() = default;
  static Json Null();
  static Json Bool(bool value);
  static Json Number(double value);
  static Json Integer(int64_t value);
  static Json UInteger(uint64_t value);
  static Json String(std::string value);
  static Json Array(std::vector<Json> value);
  static Json ObjectValue(Object value);
  static bool Parse(std::string_view text, Json* out, std::string* error);

  Type type() const {
    return type_;
  }
  bool is_null() const {
    return type_ == Type::kNull;
  }
  bool as_bool(bool fallback = false) const;
  double as_double(double fallback = 0.0) const;
  int64_t as_int(int64_t fallback = 0) const;
  bool as_integer(int64_t* out) const;
  bool as_uinteger(uint64_t* out) const;
  bool is_exact_integer() const {
    return type_ == Type::kInteger || type_ == Type::kUInteger;
  }
  std::string as_string(std::string fallback = "") const;
  const std::vector<Json>& array() const {
    return array_;
  }
  const Object& object() const {
    return object_;
  }
  const Json* Find(std::string_view key) const;
  std::string Dump() const;

 private:
  Type type_ = Type::kNull;
  bool bool_ = false;
  double number_ = 0.0;
  int64_t integer_ = 0;
  uint64_t uinteger_ = 0;
  std::string string_;
  std::vector<Json> array_;
  Object object_;
};

bool ParseFillRecord(const Json& payload, FillRecord* out, std::string* error);
Json::Object FillRecordJson(const FillRecord& record);
bool ValidateFillRecord(const FillRecord& record,
                        const PaperOrder& order,
                        std::string* error);

// ---------------------------------------------------------------------------
// HTTP client (libcurl). Credentials are injected by the caller and never
// logged, returned in errors, or persisted.
// ---------------------------------------------------------------------------
struct HttpResponse {
  long status = 0;
  std::string body;
  std::string error;
  int64_t elapsed_ms = 0;
};

class HttpClient {
 public:
  explicit HttpClient(int timeout_seconds = 30)
      : timeout_seconds_(timeout_seconds) {
  }
  HttpResponse Get(
      const std::string& url,
      const std::vector<std::pair<std::string, std::string>>& headers) const;
  HttpResponse Post(
      const std::string& url,
      const std::vector<std::pair<std::string, std::string>>& headers,
      const std::string& body) const;

 private:
  int timeout_seconds_;
};

std::vector<uint8_t> Gunzip(const std::vector<uint8_t>& input,
                            std::string* error);

// ---------------------------------------------------------------------------
// Credential resolution. Only presence flags leave this layer.
// ---------------------------------------------------------------------------
struct CredentialStatus {
  std::string service;
  bool present = false;
  std::string note;
};

struct Secret {
  std::string value;
  ~Secret() {
    std::fill(value.begin(), value.end(), '\0');
  }
  Secret() = default;
  Secret(const Secret&) = delete;
  Secret& operator=(const Secret&) = delete;
  Secret(Secret&&) = default;
  Secret& operator=(Secret&&) = default;
};

Secret ResolveSecret(const Config& config, const std::string& suffix);
std::vector<CredentialStatus> InspectCredentials(const Config& config);

// ---------------------------------------------------------------------------
// Market data providers. FYERS and Upstox are data-only.
// ---------------------------------------------------------------------------
struct FeedStatus {
  Provider provider;
  bool available = false;
  std::string detail;
  int64_t latency_ms = 0;
};

struct UniverseResult {
  std::vector<Instrument> instruments;
  std::vector<Instrument> suspended;
  size_t total_fetched = 0;
  std::string source;
  std::string error;
};

enum class PublicationFailurePoint {
  kNone,
  kAfterAuditBeforeRename,
  kAfterRenameBeforeCommit,
};

bool WriteAndSyncFile(const std::string& path,
                      const std::string& content,
                      std::string* error);
bool PublishUniverse(const std::string& path,
                     const std::string& content,
                     Journal* journal,
                     const std::string& transaction_id,
                     PublicationFailurePoint inject,
                     std::string* error);
struct UniverseRecoveryReport {
  size_t committed = 0;
  size_t recovered = 0;
  std::vector<std::string> unresolved;
};

// One immutable transaction identity record, shared by publication and
// recovery. A transaction ID names exactly one payload and one commit.
struct UniverseTransaction {
  std::string id;
  std::string digest;
  size_t size = 0;
  bool published = false;
  bool committed = false;
  size_t publishes = 0;
  size_t commits = 0;
};
// Build the set of transactions from the journal. Conflicting records are a
// journal integrity failure and always return false with a structured reason.
bool LoadUniverseTransactions(Journal* journal,
                              std::map<std::string, UniverseTransaction>* out,
                              std::string* error);

bool RecoverUniversePublication(const std::string& path,
                                Journal* journal,
                                UniverseRecoveryReport* report,
                                std::string* error);

struct HistoryParseResult {
  std::string csv;
  size_t accepted = 0;
  std::vector<std::string> errors;
};
HistoryParseResult BuildHistoryCsv(const Json& candles,
                                   const std::string& symbol);
bool PublishHistoryFile(const std::string& path,
                        const std::string& content,
                        std::string* error);

enum class HistorySyncStatus { kComplete, kPartial, kBlocked, kFailed };

struct HistorySyncResult {
  HistorySyncStatus status = HistorySyncStatus::kFailed;
  size_t expected = 0;
  size_t fetched = 0;
  size_t failed = 0;
  std::vector<std::string> failures;
  std::string manifest_digest;
};

class MarketDataService {
 public:
  MarketDataService(Config config, Journal* journal);
  std::vector<FeedStatus> CheckFeeds() const;
  UniverseResult SyncUniverse() const;
  HistorySyncResult SyncHistory(const std::vector<Instrument>& instruments,
                                std::string* error) const;
  bool FetchQuotes(const std::vector<Instrument>& instruments,
                   std::vector<Quote>* quotes,
                   std::string* error) const;
  bool MarketStatus(std::string* status, std::string* error) const;
  bool GlobalIndices(std::vector<Quote>* quotes, std::string* error) const;

 private:
  Config config_;
  Journal* journal_;
};

Quote NormalizeUpstoxQuote(const Json& item,
                           const std::string& symbol,
                           const std::string& provider_key);
double SpreadBps(const Quote& quote);
bool IsStale(const Quote& quote, int64_t now_ms, int max_age_seconds);
bool IsStaleProviderUpdate(const Quote& quote,
                           int64_t now_ms,
                           int max_age_seconds);
bool IsStaleTrade(const Quote& quote, int64_t now_ms, int max_age_seconds);
bool ProviderFreshnessUnknown(const Quote& quote);
bool TradeFreshnessUnknown(const Quote& quote);
std::vector<Instrument> ApplyEligibility(
    std::vector<Instrument> instruments,
    const std::vector<Instrument>& suspended,
    bool status_data_available,
    const Config& config);

// ---------------------------------------------------------------------------
// KDB-X / q time-series client. Data and analytics only: this class has no
// broker, risk or order-submission capability. Access is via the official KX
// C API (k.h) over loopback IPC. Only whitelisted .quant.* functions may be
// invoked; arbitrary q expressions are never accepted from callers.
// ---------------------------------------------------------------------------
class KdbxClient {
 public:
  explicit KdbxClient(KdbxConfig config);
  ~KdbxClient();
  KdbxClient(const KdbxClient&) = delete;
  KdbxClient& operator=(const KdbxClient&) = delete;

  static KdbxStatus Detect();
  bool Connect(std::string* error);
  void Disconnect();
  bool connected() const;

  bool Reset(std::string* error);
  bool IngestQuote(const Quote& quote,
                   uint64_t sequence,
                   const std::string& event_id,
                   std::string* error);
  bool IngestBar(const Candle& candle,
                 const std::string& symbol,
                 std::string* error);
  bool QueryJson(const std::string& function,
                 std::string* out,
                 std::string* error);
  bool Persist(std::string* error);
  bool LoadHistorical(std::string* out, std::string* error);
  bool SelfTest(std::string* report, std::string* error);
  bool Benchmark(std::string* report, std::string* error);

 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};

// ---------------------------------------------------------------------------
// Zerodha execution adapter. Read-only operations are supported; all order
// submission paths are hard-locked and never reach the network.
// ---------------------------------------------------------------------------
class OrderStateMachine {
 public:
  static bool IsValidTransition(OrderState from, OrderState to);
  bool Transition(OrderState to);
  OrderState state() const {
    return state_;
  }
  const std::vector<OrderState>& history() const {
    return history_;
  }

 private:
  OrderState state_ = OrderState::kNew;
  std::vector<OrderState> history_{OrderState::kNew};
};

class ZerodhaGateway {
 public:
  ZerodhaGateway(Config config, Journal* journal);
  bool ReadOnlyConnectivity(std::string* detail, std::string* error) const;
  OrderResult Submit(const OrderRequest& request) const;  // Always locked.
  std::string ComplianceGate() const;

 private:
  Config config_;
  Journal* journal_;
};

// ---------------------------------------------------------------------------
// Reports and CLI.
// ---------------------------------------------------------------------------
struct CandidateReport {
  std::string symbol;
  std::string instrument_key;
  double last = 0.0;
  double spread_bps = 0.0;
  double score = 0.0;
  std::string strategy;
  int quantity = 0;
  Money notional;
  Money total_cost;
  double break_even_bps = 0.0;
  std::string entry;
  std::string invalidation;
  std::string exit;
  int holding_sessions = 3;
  std::string rationale;
};

struct DailyReport {
  std::string generated_at_utc;
  std::string market_status;
  double regime = 0.0;
  std::vector<std::string> index_summary;
  std::vector<CandidateReport> candidates;
  std::vector<std::string> rejections;
  std::string decision = "NO TRADE";
  std::string decision_reason;
  std::string data_provenance;
  std::string dataset_status = "NOT_VERIFIED";
};

std::string RenderReport(const DailyReport& report);
std::string WriteTextFile(const std::string& path, const std::string& content);
enum class DatasetStatus { kComplete, kPartial, kBlocked, kFailed };

std::string Sha256Hex(std::string_view bytes);

struct DatasetVerification {
  DatasetStatus status = DatasetStatus::kFailed;
  std::string contract_digest;
  std::vector<std::string> errors;
  std::map<std::string, std::string> file_digests;
};

// Frozen snapshot expectations. The C++ and Python implementations both
// consume the language-neutral v2 contract described in
// docs/dataset-contract-v2.md.
struct DatasetExpectations {
  std::string snapshot_id;
  std::string calendar_id;
  int64_t generated_at_ms = 0;
  std::string provenance;
  std::vector<std::string> symbols;
  std::vector<std::string> sessions;
  std::string first_date;
  std::string last_date;
  int64_t min_rows = 1;
  std::map<std::string, std::string> instrument_ids;
};

Json::Object DatasetExpectationsJson(const DatasetExpectations& expected);
std::string CanonicalDatasetExpectations(const DatasetExpectations& expected);
std::string DatasetContractDigest(const DatasetExpectations& expected);
bool ValidateDatasetExpectations(const DatasetExpectations& expected,
                                 std::string* error);
bool ParseDatasetExpectations(const Json& value,
                              DatasetExpectations* expected,
                              std::string* error);
bool ReadDatasetExpectations(const std::string& history_dir,
                             DatasetExpectations* expected,
                             std::string* error);
bool WriteDatasetContract(const std::string& history_dir,
                          const DatasetExpectations& expected,
                          std::string* error);
bool WriteDatasetManifest(const std::string& history_dir,
                          const DatasetExpectations& expected,
                          std::string* error);

// Verification without authoritative expectations is never COMPLETE.
DatasetVerification VerifyDataset(const std::string& history_dir,
                                  std::string* error);
DatasetVerification VerifyDataset(const std::string& history_dir,
                                  const DatasetExpectations* expected,
                                  std::string* error);

bool ReadCsvCandles(const std::string& path,
                    std::vector<Candle>* candles,
                    std::string* error);

int RunCli(int argc, char** argv);

}  // namespace quant

#endif  // QUANT_QUANT_HPP_
