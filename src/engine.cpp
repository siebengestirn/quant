#include "quant.hpp"

#include <curl/curl.h>
#include <zlib.h>

#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <limits>
#include <numeric>
#include <set>
#include <sstream>

namespace quant {
namespace {

std::string Trim(std::string_view text) {
  size_t begin = 0;
  size_t end = text.size();
  while (begin < end &&
         (text[begin] == ' ' || text[begin] == '\t' || text[begin] == '\r')) {
    ++begin;
  }
  while (end > begin && (text[end - 1] == ' ' || text[end - 1] == '\t' ||
                         text[end - 1] == '\r')) {
    --end;
  }
  return std::string(text.substr(begin, end - begin));
}

std::string StripComment(const std::string& line) {
  const size_t pos = line.find('#');
  return pos == std::string::npos ? line : line.substr(0, pos);
}

bool ParseBool(std::string_view text, bool* out) {
  if (text == "true") {
    *out = true;
    return true;
  }
  if (text == "false") {
    *out = false;
    return true;
  }
  return false;
}

bool ParseArray(std::string_view text, std::vector<std::string>* out) {
  const size_t start = text.find('[');
  const size_t end = text.rfind(']');
  if (start == std::string::npos || end == std::string::npos || end <= start) {
    return false;
  }
  std::string_view inner = text.substr(start + 1, end - start - 1);
  size_t pos = 0;
  while (pos < inner.size()) {
    while (pos < inner.size() && (inner[pos] == ',' || inner[pos] == ' ' ||
                                  inner[pos] == '\n' || inner[pos] == '\t')) {
      ++pos;
    }
    if (pos >= inner.size()) break;
    if (inner[pos] != '"') return false;
    const size_t quote_end = inner.find('"', pos + 1);
    if (quote_end == std::string::npos) return false;
    out->push_back(std::string(inner.substr(pos + 1, quote_end - pos - 1)));
    pos = quote_end + 1;
  }
  return true;
}

bool ParseInteger64(const std::string& text, int64_t* out) {
  if (text.empty()) return false;
  errno = 0;
  char* end = nullptr;
  const long long value = std::strtoll(text.c_str(), &end, 10);
  if (errno == ERANGE || end != text.c_str() + text.size()) return false;
  *out = static_cast<int64_t>(value);
  return true;
}

bool ParseUnsigned64(const std::string& text, uint64_t* out) {
  if (text.empty() || text.front() == '-') return false;
  errno = 0;
  char* end = nullptr;
  const unsigned long long value = std::strtoull(text.c_str(), &end, 10);
  if (errno == ERANGE || end != text.c_str() + text.size()) return false;
  *out = static_cast<uint64_t>(value);
  return true;
}

bool ParseInteger32(const std::string& text, int32_t* out) {
  int64_t value = 0;
  if (!ParseInteger64(text, &value) ||
      value < std::numeric_limits<int32_t>::min() ||
      value > std::numeric_limits<int32_t>::max()) {
    return false;
  }
  *out = static_cast<int32_t>(value);
  return true;
}

bool ParseFloating(const std::string& text, double* out) {
  if (text.empty()) return false;
  errno = 0;
  char* end = nullptr;
  const double value = std::strtod(text.c_str(), &end);
  if (errno == ERANGE || end != text.c_str() + text.size()) return false;
  *out = value;
  return true;
}

int64_t IntegerOr(const std::map<std::string, std::string>& values,
                  const std::string& key, int64_t fallback) {
  const auto it = values.find(key);
  if (it == values.end()) return fallback;
  int64_t value = 0;
  return ParseInteger64(it->second, &value) ? value : fallback;
}

double DoubleOr(const std::map<std::string, std::string>& values,
                const std::string& key, double fallback) {
  const auto it = values.find(key);
  if (it == values.end()) return fallback;
  double value = 0.0;
  return ParseFloating(it->second, &value) ? value : fallback;
}

std::string StringOr(const std::map<std::string, std::string>& values,
                     const std::string& key, const std::string& fallback) {
  const auto it = values.find(key);
  return it == values.end() ? fallback : it->second;
}

bool BoolOr(const std::map<std::string, std::string>& values,
            const std::string& key, bool fallback) {
  const auto it = values.find(key);
  if (it == values.end()) return fallback;
  bool value = fallback;
  return ParseBool(it->second, &value) ? value : fallback;
}

std::string HexEncode(std::string_view input) {
  static constexpr char kHex[] = "0123456789abcdef";
  std::string out;
  out.reserve(input.size() * 2);
  for (unsigned char c : input) {
    out.push_back(kHex[c >> 4]);
    out.push_back(kHex[c & 0x0f]);
  }
  return out;
}

std::string HexDecode(std::string_view input) {
  auto nibble = [](char c) -> int {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
  };
  std::string out;
  for (size_t i = 0; i + 1 < input.size(); i += 2) {
    const int hi = nibble(input[i]);
    const int lo = nibble(input[i + 1]);
    if (hi < 0 || lo < 0) return "";
    out.push_back(static_cast<char>((hi << 4) | lo));
  }
  return out;
}

std::string ChecksumHex(std::string_view text) {
  std::ostringstream oss;
  oss << std::hex << std::setw(16) << std::setfill('0') << Checksum(text);
  return oss.str();
}

size_t CurlWrite(void* contents, size_t size, size_t nmemb, void* userdata) {
  const size_t bytes = size * nmemb;
  auto* body = static_cast<std::string*>(userdata);
  body->append(static_cast<const char*>(contents), bytes);
  return bytes;
}

double Mean(const std::vector<double>& values) {
  if (values.empty()) return 0.0;
  return std::accumulate(values.begin(), values.end(), 0.0) /
         static_cast<double>(values.size());
}

double StdDev(const std::vector<double>& values) {
  if (values.size() < 2) return 0.0;
  const double mean = Mean(values);
  double sum = 0.0;
  for (double value : values) {
    const double delta = value - mean;
    sum += delta * delta;
  }
  return std::sqrt(sum / static_cast<double>(values.size() - 1));
}

double Clamp(double value, double low, double high) {
  return std::max(low, std::min(high, value));
}

}  // namespace

// ---------------------------------------------------------------------------
// Money.
// ---------------------------------------------------------------------------
Money Money::FromRupees(double rupees) {
  return FromPaise(static_cast<int64_t>(std::llround(rupees * 100.0)));
}

// ---------------------------------------------------------------------------
// Configuration.
// ---------------------------------------------------------------------------
std::string_view ModeName(Mode mode) {
  switch (mode) {
    case Mode::kDisabled:
      return "DISABLED";
    case Mode::kBacktest:
      return "BACKTEST";
    case Mode::kPaper:
      return "PAPER";
    case Mode::kReadOnlyBroker:
      return "READ_ONLY_BROKER";
    case Mode::kLive:
      return "LIVE";
  }
  return "UNKNOWN";
}

Mode ParseMode(std::string_view text) {
  if (text == "BACKTEST") return Mode::kBacktest;
  if (text == "PAPER") return Mode::kPaper;
  if (text == "READ_ONLY_BROKER") return Mode::kReadOnlyBroker;
  if (text == "LIVE") return Mode::kLive;
  return Mode::kDisabled;
}

ConfigResult LoadConfig(const std::string& path) {
  ConfigResult result;
  std::ifstream input(path);
  if (!input.is_open()) {
    result.errors.push_back("cannot open config file: " + path);
    return result;
  }

  std::map<std::string, std::string> values;
  std::string section;
  std::string line;
  std::string pending;
  while (std::getline(input, line)) {
    std::string cleaned = Trim(StripComment(line));
    if (cleaned.empty()) continue;
    if (!pending.empty()) {
      pending += "\n" + cleaned;
      if (cleaned.find(']') == std::string::npos) continue;
      cleaned = pending;
      pending.clear();
    }
    if (cleaned.front() == '[' && cleaned.back() == ']') {
      section = Trim(cleaned.substr(1, cleaned.size() - 2));
      continue;
    }
    const size_t equals = cleaned.find('=');
    if (equals == std::string::npos) continue;
    std::string key = Trim(cleaned.substr(0, equals));
    std::string value = Trim(cleaned.substr(equals + 1));
    if (value.find('[') != std::string::npos &&
        value.find(']') == std::string::npos) {
      pending = cleaned;
      continue;
    }
    if (value.size() >= 2 && value.front() == '"' && value.back() == '"') {
      value = value.substr(1, value.size() - 2);
    }
    values[section + "." + key] = value;
  }
  if (!pending.empty()) {
    result.errors.push_back("unterminated array in config: " + pending);
    return result;
  }

  Config& c = result.config;
  const std::string m = "mode.";
  const std::string cap = "capital.";
  const std::string dat = "data.";
  const std::string exe = "execution.";
  const std::string uni = "universe.";
  const std::string kdb = "kdbx.";

  c.live_trading_enabled = BoolOr(values, m + "LIVE_TRADING_ENABLED", false);
  c.mode = ParseMode(StringOr(values, m + "EXECUTION_MODE", "DISABLED"));
  c.allow_margin = BoolOr(values, m + "ALLOW_MARGIN", false);
  c.allow_short_selling = BoolOr(values, m + "ALLOW_SHORT_SELLING", false);
  c.allow_withdrawals = BoolOr(values, m + "ALLOW_WITHDRAWALS", false);

  c.initial_capital =
      Money::FromRupees(DoubleOr(values, cap + "INITIAL_CAPITAL_INR", 500.0));
  c.risk_per_trade_fraction =
      DoubleOr(values, cap + "RISK_PER_TRADE_FRACTION", 0.01);
  c.max_daily_loss_fraction =
      DoubleOr(values, cap + "MAX_DAILY_LOSS_FRACTION", 0.02);
  c.max_drawdown_fraction =
      DoubleOr(values, cap + "MAX_DRAWDOWN_FRACTION", 0.10);
  c.max_open_positions =
      static_cast<int>(IntegerOr(values, cap + "MAX_OPEN_POSITIONS", 1));
  c.max_gross_exposure_fraction =
      DoubleOr(values, cap + "MAX_GROSS_EXPOSURE_FRACTION", 0.80);
  c.cash_reserve_fraction =
      DoubleOr(values, cap + "CASH_RESERVE_FRACTION", 0.10);

  c.data_dir = StringOr(values, dat + "DATA_DIR", "data");
  c.journal_path =
      StringOr(values, dat + "JOURNAL_PATH", "data/journal/events.jnl");
  c.universe_path =
      StringOr(values, dat + "UNIVERSE_PATH", "data/cache/universe.csv");
  c.history_dir = StringOr(values, dat + "HISTORY_DIR", "data/cache/history");
  c.report_dir = StringOr(values, dat + "REPORT_DIR", "data/reports");
  c.history_days =
      static_cast<size_t>(IntegerOr(values, dat + "HISTORY_DAYS", 260));
  c.universe_limit =
      static_cast<size_t>(IntegerOr(values, dat + "UNIVERSE_LIMIT", 60));
  c.shortlist_size =
      static_cast<size_t>(IntegerOr(values, dat + "SHORTLIST_SIZE", 8));
  c.stale_quote_seconds =
      static_cast<int>(IntegerOr(values, dat + "STALE_QUOTE_SECONDS", 300));
  c.http_timeout_seconds =
      static_cast<int>(IntegerOr(values, dat + "HTTP_TIMEOUT_SECONDS", 30));
  c.keychain_prefix = StringOr(values, dat + "KEYCHAIN_PREFIX", "NIRNAY_");

  c.max_spread_bps = DoubleOr(values, exe + "MAX_SPREAD_BPS", 35.0);
  c.min_traded_value_inr = DoubleOr(values, exe + "MIN_TRADED_VALUE_INR", 5e6);
  c.slippage_bps = DoubleOr(values, exe + "SLIPPAGE_BPS", 10.0);
  c.queue_capacity =
      static_cast<size_t>(IntegerOr(values, exe + "QUEUE_CAPACITY", 4096));

  auto find_array = [&values](const std::string& key) {
    std::vector<std::string> out;
    const auto it = values.find(key);
    if (it != values.end()) ParseArray(it->second, &out);
    return out;
  };
  c.seed_symbols = find_array(uni + "SEED_SYMBOLS");
  c.index_symbols = find_array(uni + "INDEX_SYMBOLS");

  c.kdbx.enabled = BoolOr(values, kdb + "ENABLED", true);
  c.kdbx.host = StringOr(values, kdb + "HOST", "127.0.0.1");
  c.kdbx.port = static_cast<int>(IntegerOr(values, kdb + "PORT", 5001));
  c.kdbx.timeout_ms =
      static_cast<int>(IntegerOr(values, kdb + "TIMEOUT_MS", 2000));
  c.kdbx.username = StringOr(values, kdb + "USERNAME", "quant");
  c.kdbx.script = StringOr(values, kdb + "SCRIPT", "q/quant.q");
  c.kdbx.hdb_dir = StringOr(values, kdb + "HDB_DIR", "data/hdb");
  c.kdbx.log_path = StringOr(values, kdb + "LOG_PATH", "data/db/q.log");
  c.kdbx.pid_path = StringOr(values, kdb + "PID_PATH", "data/db/q.pid");
  c.kdbx.batch_size =
      static_cast<size_t>(IntegerOr(values, kdb + "BATCH_SIZE", 512));

  for (const std::string& error : ValidateConfig(c)) {
    result.errors.push_back(error);
  }
  return result;
}

std::vector<std::string> ValidateConfig(const Config& config) {
  std::vector<std::string> errors;
  if (config.initial_capital <= Money::FromRupees(0.0)) {
    errors.push_back("INITIAL_CAPITAL_INR must be positive");
  }
  auto fraction = [&errors](double value, const char* name) {
    if (!(value >= 0.0 && value <= 1.0)) {
      errors.push_back(std::string(name) + " must be in [0, 1]");
    }
  };
  fraction(config.risk_per_trade_fraction, "RISK_PER_TRADE_FRACTION");
  fraction(config.max_daily_loss_fraction, "MAX_DAILY_LOSS_FRACTION");
  fraction(config.max_drawdown_fraction, "MAX_DRAWDOWN_FRACTION");
  fraction(config.max_gross_exposure_fraction, "MAX_GROSS_EXPOSURE_FRACTION");
  fraction(config.cash_reserve_fraction, "CASH_RESERVE_FRACTION");
  if (config.max_open_positions < 0) {
    errors.push_back("MAX_OPEN_POSITIONS must be non-negative");
  }
  if (config.max_spread_bps < 0.0 || config.slippage_bps < 0.0) {
    errors.push_back("cost parameters must be non-negative");
  }
  if (config.allow_margin || config.allow_short_selling) {
    errors.push_back(
        "margin and short selling are forbidden by capital policy");
  }
  if (config.allow_withdrawals) {
    errors.push_back("withdrawals are forbidden by capital policy");
  }
  if (config.kdbx.host != "127.0.0.1" && config.kdbx.host != "localhost" &&
      config.kdbx.host != "::1") {
    errors.push_back("KDB-X IPC host must be loopback");
  }
  if (config.kdbx.port < 1024 || config.kdbx.port > 65535) {
    errors.push_back("KDB-X port must be in [1024, 65535]");
  }
  if (config.live_trading_enabled || config.mode == Mode::kLive) {
    const char* authorization = std::getenv("QUANT_LIVE_AUTHORIZED");
    if (authorization == nullptr || std::string(authorization) != "1") {
      errors.push_back(
          "live mode requires explicit out-of-band authorization "
          "(QUANT_LIVE_AUTHORIZED=1) and compliance review");
    }
  }
  return errors;
}

// ---------------------------------------------------------------------------
// Transaction costs: Zerodha equity schedule, version 2026-11-25-v1.
// VERIFIED against https://zerodha.com/charges/ on 2026-11-25.
// ESTIMATED where the schedule varies by account/segment (DP, stamp cap).
// ---------------------------------------------------------------------------
CostBreakdown EstimateCosts(Money turnover, bool delivery, bool is_buy,
                            double slippage_bps) {
  CostBreakdown result;
  if (turnover <= Money::FromRupees(0.0) || slippage_bps < 0.0) return result;

  const double value = turnover.rupees();
  const double brokerage_rate = delivery ? 0.0 : 0.0003;
  const double brokerage_inr = std::min(value * brokerage_rate, 20.0);
  result.brokerage = Money::FromRupees(brokerage_inr);

  double stt_rate = 0.0;
  if (delivery) {
    stt_rate = 0.001;  // both sides.
  } else if (!is_buy) {
    stt_rate = 0.00025;  // sell side only.
  }
  result.stt = Money::FromRupees(value * stt_rate);

  result.exchange_fee = Money::FromRupees(value * 0.0000307);  // NSE cash.
  result.sebi = Money::FromPaise(static_cast<int64_t>(
      std::llround(static_cast<double>(turnover.paise()) * 1e-6)));
  const double gst_base = result.brokerage.rupees() +
                          result.exchange_fee.rupees() + result.sebi.rupees();
  result.gst = Money::FromRupees(gst_base * 0.18);

  if (is_buy) {
    const double stamp_rate = delivery ? 0.00015 : 0.00003;
    result.stamp = Money::FromRupees(value * stamp_rate);
  }
  if (delivery && !is_buy) {
    result.dp = Money::FromRupees(15.34);  // ESTIMATED: per scrip, sell side.
  }
  result.slippage = Money::FromRupees(value * slippage_bps / 10000.0);

  result.total = result.brokerage + result.stt + result.exchange_fee +
                 result.gst + result.sebi + result.stamp + result.dp +
                 result.slippage;
  return result;
}

double BreakEvenMoveBps(Money notional, const CostBreakdown& entry,
                        const CostBreakdown& exit_costs) {
  if (notional <= Money::FromRupees(0.0)) return 0.0;
  const double total =
      static_cast<double>(entry.total.paise() + exit_costs.total.paise());
  return total / static_cast<double>(notional.paise()) * 10000.0;
}

std::string FormatCosts(const CostBreakdown& costs) {
  std::ostringstream oss;
  oss << "brokerage=" << costs.brokerage.rupees()
      << " stt=" << costs.stt.rupees()
      << " exchange=" << costs.exchange_fee.rupees()
      << " sebi=" << costs.sebi.rupees() << " gst=" << costs.gst.rupees()
      << " stamp=" << costs.stamp.rupees() << " dp=" << costs.dp.rupees()
      << " slippage=" << costs.slippage.rupees()
      << " total=" << costs.total.rupees() << " (" << costs.version << ")";
  return oss.str();
}

// ---------------------------------------------------------------------------
// Portfolio accounting.
// ---------------------------------------------------------------------------
Portfolio::Portfolio(Money initial_capital) {
  ledger_.initial_capital = initial_capital;
  ledger_.cash = initial_capital;
  ledger_.invested = Money::FromRupees(0.0);
  ledger_.realized_pnl = Money::FromRupees(0.0);
  ledger_.unrealized_pnl = Money::FromRupees(0.0);
  ledger_.brokerage = Money::FromRupees(0.0);
  ledger_.statutory = Money::FromRupees(0.0);
  ledger_.operating = Money::FromRupees(0.0);
  ledger_.estimated_tax = Money::FromRupees(0.0);
  ledger_.peak_equity = initial_capital;
  ledger_.external_flows = Money::FromRupees(0.0);
  audit_.push_back("INITIAL_CAPITAL " +
                   std::to_string(initial_capital.paise()));
}

bool Portfolio::Buy(const std::string& symbol, int quantity, Money price,
                    const CostBreakdown& costs, std::string* error) {
  if (quantity <= 0 || price <= Money::FromRupees(0.0)) {
    if (error) *error = "invalid buy quantity or price";
    return false;
  }
  const Money notional = Money::FromPaise(price.paise() * quantity);
  const Money required = notional + costs.total;
  if (required > ledger_.cash) {
    if (error) *error = "insufficient cash: required exceeds available";
    return false;
  }
  auto it = positions_.find(symbol);
  if (it == positions_.end()) {
    Position position;
    position.symbol = symbol;
    position.quantity = quantity;
    position.avg_price = price;
    position.last_price = price;
    positions_[symbol] = position;
  } else {
    const int total = it->second.quantity + quantity;
    const int64_t weighted =
        it->second.avg_price.paise() * it->second.quantity +
        price.paise() * quantity;
    it->second.avg_price = Money::FromPaise(weighted / total);
    it->second.quantity = total;
    it->second.last_price = price;
  }
  ledger_.cash -= required;
  ledger_.invested += notional;
  ledger_.realized_pnl -= costs.total;
  ledger_.brokerage += costs.brokerage;
  ledger_.statutory += costs.stt + costs.exchange_fee + costs.gst + costs.sebi +
                       costs.stamp + costs.dp;
  ++ledger_.trade_count;
  audit_.push_back("BUY " + symbol + " qty=" + std::to_string(quantity) +
                   " price_paise=" + std::to_string(price.paise()) +
                   " cost_paise=" + std::to_string(costs.total.paise()));
  return true;
}

bool Portfolio::Sell(const std::string& symbol, int quantity, Money price,
                     const CostBreakdown& costs, std::string* error) {
  auto it = positions_.find(symbol);
  if (it == positions_.end() || it->second.quantity < quantity ||
      quantity <= 0) {
    if (error) *error = "sell quantity exceeds position";
    return false;
  }
  if (price <= Money::FromRupees(0.0)) {
    if (error) *error = "invalid sell price";
    return false;
  }
  const Money notional = Money::FromPaise(price.paise() * quantity);
  const Money basis = Money::FromPaise(it->second.avg_price.paise() * quantity);
  const Money pnl = notional - basis - costs.total;
  ledger_.cash += notional - costs.total;
  ledger_.invested -= basis;
  ledger_.realized_pnl += pnl;
  ledger_.brokerage += costs.brokerage;
  ledger_.statutory += costs.stt + costs.exchange_fee + costs.gst + costs.sebi +
                       costs.stamp + costs.dp;
  ++ledger_.trade_count;
  it->second.quantity -= quantity;
  if (it->second.quantity == 0) {
    positions_.erase(it);
  } else {
    it->second.last_price = price;
  }
  audit_.push_back("SELL " + symbol + " qty=" + std::to_string(quantity) +
                   " price_paise=" + std::to_string(price.paise()) +
                   " pnl_paise=" + std::to_string(pnl.paise()));
  return true;
}

void Portfolio::MarkToMarket(const std::string& symbol, Money price) {
  auto it = positions_.find(symbol);
  if (it == positions_.end()) return;
  it->second.last_price = price;
  int64_t unrealized = 0;
  for (const auto& [key, position] : positions_) {
    unrealized += (position.last_price.paise() - position.avg_price.paise()) *
                  static_cast<int64_t>(position.quantity);
    (void)key;
  }
  ledger_.unrealized_pnl = Money::FromPaise(unrealized);
  const Money current = equity();
  if (current > ledger_.peak_equity) ledger_.peak_equity = current;
}

void Portfolio::RecordOperatingExpense(Money amount) {
  ledger_.cash -= amount;
  ledger_.operating += amount;
  audit_.push_back("OPERATING_EXPENSE " + std::to_string(amount.paise()));
}

void Portfolio::RecordExternalFlow(Money amount, const std::string& reason) {
  ledger_.cash += amount;
  ledger_.external_flows += amount;
  audit_.push_back("RECONCILIATION_EXCEPTION " +
                   std::to_string(amount.paise()) + " " + reason);
}

Money Portfolio::equity() const {
  return ledger_.cash + ledger_.invested + ledger_.unrealized_pnl;
}

Money Portfolio::available_cash() const { return ledger_.cash; }

Money Portfolio::drawdown() const { return ledger_.peak_equity - equity(); }

double Portfolio::drawdown_fraction() const {
  if (ledger_.peak_equity <= Money::FromRupees(0.0)) return 0.0;
  return static_cast<double>(drawdown().paise()) /
         static_cast<double>(ledger_.peak_equity.paise());
}

// ---------------------------------------------------------------------------
// Central risk engine.
// ---------------------------------------------------------------------------
RiskDecision RiskEngine::Evaluate(const Signal& signal, const Quote& quote,
                                  const Portfolio& portfolio,
                                  int64_t now_ms) const {
  RiskDecision decision;
  decision.notional = Money::FromRupees(0.0);
  auto reject = [&decision](const std::string& reason) {
    decision.approved = false;
    decision.quantity = 0;
    decision.reason = reason;
    return decision;
  };

  if (!signal.tradable)
    return reject("signal rejected upstream: " + signal.rejection_reason);
  if (config_.live_trading_enabled && config_.mode != Mode::kLive) {
    return reject("fail-closed: live flag inconsistent with execution mode");
  }
  if (IsStale(quote, now_ms, config_.stale_quote_seconds)) {
    return reject("stale market data");
  }
  if (!(quote.last > 0.0) || !(quote.bid > 0.0) || !(quote.ask >= quote.bid)) {
    return reject("invalid or missing quote depth");
  }
  if (SpreadBps(quote) > config_.max_spread_bps) {
    return reject("spread exceeds limit");
  }
  if (portfolio.open_position_count() >= config_.max_open_positions) {
    return reject("maximum open positions reached");
  }
  if (portfolio.drawdown_fraction() >= config_.max_drawdown_fraction) {
    return reject("maximum drawdown reached");
  }

  const double price = quote.last;
  const Money equity = portfolio.equity();
  const Money reserve =
      Money::FromRupees(equity.rupees() * config_.cash_reserve_fraction);
  const double risk_budget = std::min(
      config_.initial_capital.rupees() * config_.risk_per_trade_fraction,
      equity.rupees() * config_.risk_per_trade_fraction);
  const double vol = std::max(signal.features.vol20, 0.005);
  const double risk_per_share = price * vol * 2.0;  // 2-sigma daily move.
  if (!(risk_per_share > 0.0)) return reject("invalid volatility estimate");

  const double risk_qty = std::floor(risk_budget / risk_per_share);
  const double spendable =
      std::max(0.0, portfolio.available_cash().rupees() - reserve.rupees());
  const double cash_qty =
      std::floor(spendable / (price * (1.0 + config_.slippage_bps / 10000.0)));
  const double exposure_cap =
      std::floor(equity.rupees() * config_.max_gross_exposure_fraction / price);
  const double quantity =
      std::min({risk_qty, cash_qty, exposure_cap,
                static_cast<double>(quote.bid_qty > 0 ? quote.bid_qty : 0)});
  if (!(quantity >= 1.0)) {
    return reject("minimum one share violates risk/cash/liquidity budget");
  }

  decision.approved = true;
  decision.quantity = static_cast<int>(quantity);
  decision.notional = Money::FromRupees(price * decision.quantity);
  decision.reason =
      "approved: vol-adjusted qty within cash and exposure limits";
  return decision;
}

// ---------------------------------------------------------------------------
// Features and strategies.
// ---------------------------------------------------------------------------
FeatureSet ComputeFeatures(const std::vector<Candle>& candles) {
  FeatureSet features;
  if (candles.size() < 21) return features;
  auto return_over = [&candles](size_t periods) {
    if (candles.size() <= periods) return 0.0;
    const double base = candles[candles.size() - 1 - periods].close;
    return base > 0.0 ? candles.back().close / base - 1.0 : 0.0;
  };
  features.ret5 = return_over(5);
  features.ret20 = return_over(20);
  features.ret60 = return_over(60);
  features.ret120 = return_over(120);

  std::vector<double> returns;
  std::vector<double> volumes;
  for (size_t i = candles.size() - 20; i < candles.size(); ++i) {
    if (i > 0 && candles[i - 1].close > 0.0) {
      returns.push_back(candles[i].close / candles[i - 1].close - 1.0);
    }
    volumes.push_back(candles[i].volume);
  }
  features.vol20 = StdDev(returns);
  const double average_volume = Mean(volumes);
  features.volume_surprise =
      average_volume > 0.0 ? volumes.back() / average_volume - 1.0 : 0.0;

  double sma20 = 0.0;
  double sma60 = 0.0;
  double sma120 = 0.0;
  for (size_t i = candles.size() - 20; i < candles.size(); ++i) {
    sma20 += candles[i].close / 20.0;
  }
  if (candles.size() >= 60) {
    for (size_t i = candles.size() - 60; i < candles.size(); ++i) {
      sma60 += candles[i].close / 60.0;
    }
  }
  if (candles.size() >= 120) {
    for (size_t i = candles.size() - 120; i < candles.size(); ++i) {
      sma120 += candles[i].close / 120.0;
    }
  }
  int bullish = 0;
  int checks = 1;
  if (candles.back().close > sma20) ++bullish;
  if (candles.size() >= 60) {
    ++checks;
    if (sma20 > sma60) ++bullish;
  }
  if (candles.size() >= 120) {
    ++checks;
    if (sma20 > sma120) ++bullish;
  }
  features.trend = static_cast<double>(bullish) / static_cast<double>(checks);

  double highest = candles.back().high;
  double lowest = candles.back().low;
  for (size_t i = candles.size() - 20; i < candles.size(); ++i) {
    highest = std::max(highest, candles[i].high);
    lowest = std::min(lowest, candles[i].low);
  }
  const double range = highest - lowest;
  features.breakout =
      range > 0.0 ? Clamp((candles.back().close - lowest) / range * 2.0 - 1.0,
                          -1.0, 1.0)
                  : 0.0;

  double typical = 0.0;
  for (size_t i = candles.size() - 20; i < candles.size(); ++i) {
    typical +=
        (candles[i].high + candles[i].low + candles[i].close) / 3.0 / 20.0;
  }
  features.vwap_dev =
      typical > 0.0 ? candles.back().close / typical - 1.0 : 0.0;
  features.valid = true;
  return features;
}

double CrossSectionalMomentumScore(const FeatureSet& f) {
  if (!f.valid) return 0.0;
  const double vol_adjust = 1.0 / std::max(f.vol20, 0.005);
  const double raw =
      0.15 * f.ret5 + 0.25 * f.ret20 + 0.35 * f.ret60 + 0.25 * f.ret120;
  return Clamp(raw * vol_adjust * 0.05, -1.0, 1.0);
}

double TimeSeriesMomentumScore(const FeatureSet& f) {
  if (!f.valid) return 0.0;
  return Clamp((f.trend - 0.5) * 2.0 + f.breakout * 0.3, -1.0, 1.0);
}

double VolumeBreakoutScore(const FeatureSet& f) {
  if (!f.valid) return 0.0;
  return Clamp(f.breakout * 0.6 + Clamp(f.volume_surprise, -1.0, 2.0) * 0.2 +
                   Clamp(f.vwap_dev * 20.0, -1.0, 1.0) * 0.2,
               -1.0, 1.0);
}

double RegimeScore(const std::vector<Candle>& index_candles) {
  if (index_candles.size() < 21) return 0.0;
  const FeatureSet f = ComputeFeatures(index_candles);
  const double vol_penalty = Clamp(0.02 / std::max(f.vol20, 0.002), 0.25, 1.5);
  return Clamp((0.6 * f.ret20 / std::max(f.vol20, 0.002) * 0.05 +
                0.4 * (f.trend - 0.5) * 2.0) *
                   vol_penalty,
               -1.0, 1.0);
}

double EnsembleScore(const FeatureSet& f, double regime) {
  // A = w1*M + w2*B + w3*V + w4*R - w5*C + w6*Q, fixed research weights.
  // ESTIMATED weights: not fitted. Validated weights require walk-forward data.
  const double momentum = CrossSectionalMomentumScore(f);
  const double breakout = TimeSeriesMomentumScore(f);
  const double volume = VolumeBreakoutScore(f);
  const double residual =
      momentum - 0.5 * regime;  // crude market-adjusted alpha.
  const double quality = f.valid ? 1.0 : -1.0;
  const double cost = 0.12;  // normalised cost penalty, see cost model.
  const double score = 0.30 * momentum + 0.22 * breakout + 0.18 * volume +
                       0.12 * residual + 0.10 * quality - 0.08 * cost;
  return Clamp(score, -1.0, 1.0);
}

// ---------------------------------------------------------------------------
// Deterministic event-driven backtester.
// ---------------------------------------------------------------------------
BacktestMetrics RunBacktest(
    const std::map<std::string, std::vector<Candle>>& history,
    const Config& config) {
  BacktestMetrics metrics;
  metrics.gross_pnl = Money::FromRupees(0.0);
  metrics.net_pnl = Money::FromRupees(0.0);
  metrics.max_drawdown = Money::FromRupees(0.0);

  std::set<std::string> dates;
  for (const auto& [symbol, candles] : history) {
    (void)symbol;
    for (const Candle& candle : candles) dates.insert(candle.date);
  }
  if (dates.size() < 130) return metrics;

  auto index_it = history.find("NIFTY 50");
  const std::vector<Candle> empty;
  const std::vector<Candle>& index =
      index_it == history.end() ? empty : index_it->second;

  Portfolio portfolio(config.initial_capital);
  std::vector<double> equity_curve;
  std::string open_symbol;
  size_t entry_index = 0;
  int hold_sessions = 0;
  Money entry_price = Money::FromRupees(0.0);
  int64_t gross_wins = 0;
  int64_t gross_losses = 0;

  std::vector<std::string> ordered_dates(dates.begin(), dates.end());
  for (size_t day = 120; day + 1 < ordered_dates.size(); ++day) {
    const std::string& decision_date = ordered_dates[day];

    if (!open_symbol.empty()) {
      auto pos_it = history.find(open_symbol);
      if (pos_it != history.end()) {
        const auto& series = pos_it->second;
        size_t current_index = series.size();
        for (size_t i = 0; i < series.size(); ++i) {
          if (series[i].date == decision_date) {
            current_index = i;
            break;
          }
        }
        if (current_index < series.size()) {
          const Candle& candle = series[current_index];
          portfolio.MarkToMarket(open_symbol, Money::FromRupees(candle.close));
          const bool stop = candle.low <= entry_price.rupees() * 0.95;
          if (current_index >=
                  entry_index + static_cast<size_t>(hold_sessions) ||
              stop) {
            const Money exit_price = Money::FromRupees(candle.open);
            auto position = portfolio.positions().find(open_symbol);
            if (position != portfolio.positions().end()) {
              const int qty = position->second.quantity;
              const CostBreakdown costs =
                  EstimateCosts(Money::FromPaise(exit_price.paise() * qty),
                                false, false, config.slippage_bps);
              const Money basis = position->second.avg_price;  // per share.
              const int64_t trade_gross = (exit_price.paise() - basis.paise()) *
                                          static_cast<int64_t>(qty);
              metrics.gross_pnl += Money::FromPaise(trade_gross);
              if (trade_gross > 0)
                gross_wins += trade_gross;
              else
                gross_losses -= trade_gross;
              portfolio.Sell(open_symbol, qty, exit_price, costs, nullptr);
              ++metrics.trades;
              if (exit_price > basis) ++metrics.wins;
            }
            open_symbol.clear();
          }
        }
      }
    }

    std::vector<Candle> index_window;
    for (const Candle& candle : index) {
      if (candle.date <= decision_date) index_window.push_back(candle);
    }
    const double regime = RegimeScore(index_window);
    if (regime < -0.2 ||
        portfolio.open_position_count() >= config.max_open_positions) {
      metrics.max_drawdown =
          std::max(metrics.max_drawdown, portfolio.drawdown());
      equity_curve.push_back(portfolio.equity().rupees());
      continue;
    }

    double best_score = 0.35;
    std::string best_symbol;
    for (const auto& [symbol, candles] : history) {
      if (symbol == "NIFTY 50" || symbol == "INDIA VIX") continue;
      std::vector<Candle> window;
      for (const Candle& candle : candles) {
        if (candle.date <= decision_date) window.push_back(candle);
      }
      if (window.size() < 130) continue;
      const FeatureSet features = ComputeFeatures(window);
      if (!features.valid) continue;
      const double score = EnsembleScore(features, regime);
      if (score > best_score) {
        best_score = score;
        best_symbol = symbol;
      }
    }

    if (!best_symbol.empty()) {
      const std::string& execution_date = ordered_dates[day + 1];
      auto series_it = history.find(best_symbol);
      double execution_price = 0.0;
      size_t execution_index = 0;
      if (series_it != history.end()) {
        for (size_t i = 0; i < series_it->second.size(); ++i) {
          if (series_it->second[i].date == execution_date) {
            execution_price = series_it->second[i].open;
            execution_index = i;
            break;
          }
        }
      }
      if (execution_price > 0.0) {
        const double risk_budget =
            config.initial_capital.rupees() * config.risk_per_trade_fraction;
        const auto features = ComputeFeatures([&] {
          std::vector<Candle> window;
          for (const Candle& candle : series_it->second) {
            if (candle.date <= decision_date) window.push_back(candle);
          }
          return window;
        }());
        const double vol = std::max(features.vol20, 0.005);
        const double qty_double = std::min(
            risk_budget / (execution_price * vol * 2.0),
            (portfolio.available_cash().rupees() * 0.85) / execution_price);
        const int qty = static_cast<int>(std::floor(qty_double));
        if (qty >= 1) {
          const Money price = Money::FromRupees(execution_price);
          const CostBreakdown costs =
              EstimateCosts(Money::FromPaise(price.paise() * qty), false, true,
                            config.slippage_bps);
          std::string error;
          if (portfolio.Buy(best_symbol, qty, price, costs, &error)) {
            open_symbol = best_symbol;
            entry_index = execution_index;
            entry_price = price;
            hold_sessions = 3;
            metrics.net_pnl -= costs.total;
          }
        }
      }
    }

    metrics.max_drawdown = std::max(metrics.max_drawdown, portfolio.drawdown());
    equity_curve.push_back(portfolio.equity().rupees());
  }

  for (const auto& [symbol, position] : portfolio.positions()) {
    (void)position;
    auto series_it = history.find(symbol);
    if (series_it == history.end() || series_it->second.empty()) continue;
    const Money price = Money::FromRupees(series_it->second.back().close);
    portfolio.MarkToMarket(symbol, price);
  }

  metrics.net_pnl = portfolio.equity() - config.initial_capital;
  metrics.realized_pnl = portfolio.ledger().realized_pnl;
  metrics.unrealized_pnl = portfolio.ledger().unrealized_pnl;
  metrics.brokerage = portfolio.ledger().brokerage;
  metrics.statutory = portfolio.ledger().statutory;
  metrics.open_positions = portfolio.open_position_count();
  metrics.final_equity = portfolio.equity().rupees();
  metrics.profit_factor =
      gross_losses > 0
          ? static_cast<double>(gross_wins) / static_cast<double>(gross_losses)
          : (gross_wins > 0 ? 999.0 : 0.0);
  const double gross = std::abs(metrics.gross_pnl.rupees());
  metrics.cost_to_gross =
      gross > 0.0 ? std::abs(portfolio.ledger().brokerage.rupees() +
                             portfolio.ledger().statutory.rupees()) /
                        gross
                  : 0.0;
  std::vector<double> returns;
  for (size_t i = 1; i < equity_curve.size(); ++i) {
    if (equity_curve[i - 1] > 0.0) {
      returns.push_back(equity_curve[i] / equity_curve[i - 1] - 1.0);
    }
  }
  const double mean = Mean(returns);
  const double sigma = StdDev(returns);
  metrics.sharpe = sigma > 0.0 ? mean / sigma * std::sqrt(252.0) : 0.0;
  double downside = 0.0;
  for (double value : returns) {
    if (value < 0.0) downside += value * value;
  }
  const double downside_sigma =
      returns.empty()
          ? 0.0
          : std::sqrt(downside / static_cast<double>(returns.size()));
  metrics.sortino =
      downside_sigma > 0.0 ? mean / downside_sigma * std::sqrt(252.0) : 0.0;
  return metrics;
}

std::vector<double> BootstrapSharpeCI(const std::vector<double>& returns,
                                      uint64_t seed, size_t draws) {
  if (returns.empty() || draws == 0) return {0.0, 0.0, 0.0};
  uint64_t state = seed == 0 ? 0x9e3779b97f4a7c15ULL : seed;
  auto next = [&state]() {
    state ^= state << 13;
    state ^= state >> 7;
    state ^= state << 17;
    return state;
  };
  std::vector<double> sharpes;
  sharpes.reserve(draws);
  for (size_t draw = 0; draw < draws; ++draw) {
    std::vector<double> sample;
    sample.reserve(returns.size());
    for (size_t i = 0; i < returns.size(); ++i) {
      sample.push_back(returns[next() % returns.size()]);
    }
    const double mean = Mean(sample);
    const double sigma = StdDev(sample);
    sharpes.push_back(sigma > 0.0 ? mean / sigma * std::sqrt(252.0) : 0.0);
  }
  std::sort(sharpes.begin(), sharpes.end());
  const size_t low = static_cast<size_t>(0.025 * static_cast<double>(draws));
  const size_t high = static_cast<size_t>(0.975 * static_cast<double>(draws));
  return {sharpes[std::min(low, sharpes.size() - 1)],
          sharpes[sharpes.size() / 2],
          sharpes[std::min(high, sharpes.size() - 1)]};
}

double DeflatedSharpe(double sharpe, size_t trials, size_t observations) {
  // ESTIMATED: transparent conservative multiple-testing haircut, not the full
  // Bailey-Lopez de Prado DSR (which needs the full trial return distribution).
  if (observations < 3) return 0.0;
  const double denominator = std::log(static_cast<double>(observations) + 1.0);
  if (denominator <= 0.0) return 0.0;
  const double factor = std::sqrt(std::max(
      0.0, 1.0 - std::log(static_cast<double>(trials) + 1.0) / denominator));
  return sharpe * factor;
}

// ---------------------------------------------------------------------------
// Journal.
// ---------------------------------------------------------------------------
uint64_t Checksum(std::string_view text) {
  uint64_t hash = 0xcbf29ce484222325ULL;
  for (unsigned char c : text) {
    hash ^= c;
    hash *= 0x100000001b3ULL;
  }
  return hash;
}

std::string EventId(uint64_t sequence, std::string_view payload) {
  std::ostringstream oss;
  oss << "evt-" << std::setfill('0') << std::setw(12) << sequence << "-"
      << std::hex << Checksum(payload);
  return oss.str();
}

bool Journal::Open(const std::string& path, std::string* error) {
  path_ = path;
  const size_t slash = path_.find_last_of('/');
  if (slash != std::string::npos) {
    std::string directory = path_.substr(0, slash);
    std::filesystem::create_directories(directory);
  }
  std::ifstream input(path_);
  std::string line;
  uint64_t maximum = 0;
  while (std::getline(input, line)) {
    if (!line.empty()) ++size_;
    const size_t pipe = line.find('|');
    if (pipe == std::string::npos) continue;
    uint64_t sequence = 0;
    if (ParseUnsigned64(line.substr(0, pipe), &sequence)) {
      maximum = std::max(maximum, sequence);
    }
  }
  next_sequence_ = maximum + 1;
  if (error) error->clear();
  return true;
}

bool Journal::Append(Event* event, std::string* error) {
  if (path_.empty()) {
    if (error) *error = "journal not opened";
    return false;
  }
  event->sequence = next_sequence_++;
  if (event->id.empty()) event->id = EventId(event->sequence, event->payload);
  event->timestamp_ms =
      event->timestamp_ms == 0
          ? std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::system_clock::now().time_since_epoch())
                .count()
          : event->timestamp_ms;
  std::ostringstream canonical;
  canonical << event->id << '|' << static_cast<int>(event->type) << '|'
            << event->timestamp_ms << '|' << event->schema_version << '|'
            << event->payload;
  event->checksum = ChecksumHex(canonical.str());

  std::ofstream output(path_, std::ios::app);
  if (!output.is_open()) {
    if (error) *error = "cannot open journal for append";
    return false;
  }
  output << event->sequence << '|' << event->id << '|'
         << static_cast<int>(event->type) << '|' << event->timestamp_ms << '|'
         << event->schema_version << '|' << HexEncode(event->payload) << '|'
         << event->checksum << '\n';
  if (!output.good()) {
    if (error) *error = "journal write failed";
    return false;
  }
  ++size_;
  if (error) error->clear();
  return true;
}

std::vector<Event> Journal::Replay(std::string* error) const {
  std::vector<Event> events;
  std::ifstream input(path_);
  if (!input.is_open()) {
    if (error) *error = "cannot open journal for replay";
    return events;
  }
  std::string line;
  uint64_t expected = 1;
  while (std::getline(input, line)) {
    if (line.empty()) continue;
    std::vector<std::string> fields;
    std::stringstream ss(line);
    std::string field;
    while (std::getline(ss, field, '|')) fields.push_back(field);
    if (fields.size() != 7) {
      if (error) *error = "malformed journal line";
      return {};
    }
    Event event;
    int32_t type = 0;
    int32_t schema = 0;
    if (!ParseUnsigned64(fields[0], &event.sequence) ||
        !ParseInteger32(fields[2], &type) ||
        !ParseInteger64(fields[3], &event.timestamp_ms) ||
        !ParseInteger32(fields[4], &schema)) {
      if (error) *error = "non-numeric journal field";
      return {};
    }
    event.type = static_cast<EventType>(type);
    event.schema_version = schema;
    event.id = fields[1];
    event.payload = HexDecode(fields[5]);
    event.checksum = fields[6];
    std::ostringstream canonical;
    canonical << event.id << '|' << static_cast<int>(event.type) << '|'
              << event.timestamp_ms << '|' << event.schema_version << '|'
              << event.payload;
    if (ChecksumHex(canonical.str()) != event.checksum) {
      if (error)
        *error = "journal checksum mismatch at sequence " +
                 std::to_string(event.sequence);
      return {};
    }
    if (event.sequence != expected) {
      if (error)
        *error = "journal sequence gap at " + std::to_string(event.sequence);
      return {};
    }
    ++expected;
    events.push_back(event);
  }
  if (error) error->clear();
  return events;
}

// ---------------------------------------------------------------------------
// Minimal JSON parser.
// ---------------------------------------------------------------------------
class JsonParser {
 public:
  explicit JsonParser(std::string_view text) : text_(text) {}

  bool Parse(Json* out, std::string* error) {
    SkipSpace();
    if (!Value(out)) {
      if (error) *error = "JSON parse error at offset " + std::to_string(pos_);
      return false;
    }
    SkipSpace();
    return pos_ == text_.size();
  }

 private:
  void SkipSpace() {
    while (pos_ < text_.size() &&
           (text_[pos_] == ' ' || text_[pos_] == '\n' || text_[pos_] == '\r' ||
            text_[pos_] == '\t')) {
      ++pos_;
    }
  }

  bool Value(Json* out) {
    SkipSpace();
    if (pos_ >= text_.size()) return false;
    const char c = text_[pos_];
    if (c == '{') return Object(out);
    if (c == '[') return Array(out);
    if (c == '"') {
      std::string value;
      if (!String(&value)) return false;
      *out = Json::String(value);
      return true;
    }
    if (text_.substr(pos_, 4) == "true") {
      pos_ += 4;
      *out = Json::Bool(true);
      return true;
    }
    if (text_.substr(pos_, 5) == "false") {
      pos_ += 5;
      *out = Json::Bool(false);
      return true;
    }
    if (text_.substr(pos_, 4) == "null") {
      pos_ += 4;
      *out = Json::Null();
      return true;
    }
    return Number(out);
  }

  bool String(std::string* out) {
    if (text_[pos_] != '"') return false;
    ++pos_;
    out->clear();
    while (pos_ < text_.size()) {
      const char c = text_[pos_++];
      if (c == '"') return true;
      if (c != '\\') {
        out->push_back(c);
        continue;
      }
      if (pos_ >= text_.size()) return false;
      const char escape = text_[pos_++];
      switch (escape) {
        case 'n':
          out->push_back('\n');
          break;
        case 'r':
          out->push_back('\r');
          break;
        case 't':
          out->push_back('\t');
          break;
        case '"':
          out->push_back('"');
          break;
        case '\\':
          out->push_back('\\');
          break;
        case '/':
          out->push_back('/');
          break;
        case 'u': {
          if (pos_ + 4 > text_.size()) return false;
          const unsigned code = static_cast<unsigned>(
              std::stoul(std::string(text_.substr(pos_, 4)), nullptr, 16));
          pos_ += 4;
          if (code < 0x80) {
            out->push_back(static_cast<char>(code));
          } else if (code < 0x800) {
            out->push_back(static_cast<char>(0xc0 | (code >> 6)));
            out->push_back(static_cast<char>(0x80 | (code & 0x3f)));
          } else {
            out->push_back(static_cast<char>(0xe0 | (code >> 12)));
            out->push_back(static_cast<char>(0x80 | ((code >> 6) & 0x3f)));
            out->push_back(static_cast<char>(0x80 | (code & 0x3f)));
          }
          break;
        }
        default:
          return false;
      }
    }
    return false;
  }

  bool Number(Json* out) {
    const size_t start = pos_;
    if (pos_ < text_.size() && (text_[pos_] == '-' || text_[pos_] == '+'))
      ++pos_;
    bool digits = false;
    while (pos_ < text_.size() &&
           ((text_[pos_] >= '0' && text_[pos_] <= '9') || text_[pos_] == '.' ||
            text_[pos_] == 'e' || text_[pos_] == 'E' || text_[pos_] == '-' ||
            text_[pos_] == '+')) {
      if (text_[pos_] >= '0' && text_[pos_] <= '9') digits = true;
      ++pos_;
    }
    if (!digits) return false;
    double value = 0.0;
    if (!ParseFloating(std::string(text_.substr(start, pos_ - start)),
                       &value)) {
      return false;
    }
    *out = Json::Number(value);
    return true;
  }

  bool Array(Json* out) {
    ++pos_;
    std::vector<Json> values;
    SkipSpace();
    if (pos_ < text_.size() && text_[pos_] == ']') {
      ++pos_;
      *out = Json::Array(values);
      return true;
    }
    while (pos_ < text_.size()) {
      Json value;
      if (!Value(&value)) return false;
      values.push_back(value);
      SkipSpace();
      if (pos_ >= text_.size()) return false;
      if (text_[pos_] == ',') {
        ++pos_;
        continue;
      }
      if (text_[pos_] == ']') {
        ++pos_;
        *out = Json::Array(values);
        return true;
      }
      return false;
    }
    return false;
  }

  bool Object(Json* out) {
    ++pos_;
    Json::Object values;
    SkipSpace();
    if (pos_ < text_.size() && text_[pos_] == '}') {
      ++pos_;
      *out = Json::ObjectValue(values);
      return true;
    }
    while (pos_ < text_.size()) {
      SkipSpace();
      std::string key;
      if (!String(&key)) return false;
      SkipSpace();
      if (pos_ >= text_.size() || text_[pos_] != ':') return false;
      ++pos_;
      Json value;
      if (!Value(&value)) return false;
      values[key] = value;
      SkipSpace();
      if (pos_ >= text_.size()) return false;
      if (text_[pos_] == ',') {
        ++pos_;
        continue;
      }
      if (text_[pos_] == '}') {
        ++pos_;
        *out = Json::ObjectValue(values);
        return true;
      }
      return false;
    }
    return false;
  }

  std::string_view text_;
  size_t pos_ = 0;
};

Json Json::Null() { return Json(); }
Json Json::Bool(bool value) {
  Json json;
  json.type_ = Type::kBool;
  json.bool_ = value;
  return json;
}
Json Json::Number(double value) {
  Json json;
  json.type_ = Type::kNumber;
  json.number_ = value;
  return json;
}
Json Json::String(std::string value) {
  Json json;
  json.type_ = Type::kString;
  json.string_ = std::move(value);
  return json;
}
Json Json::Array(std::vector<Json> value) {
  Json json;
  json.type_ = Type::kArray;
  json.array_ = std::move(value);
  return json;
}
Json Json::ObjectValue(Object value) {
  Json json;
  json.type_ = Type::kObject;
  json.object_ = std::move(value);
  return json;
}
bool Json::Parse(std::string_view text, Json* out, std::string* error) {
  return JsonParser(text).Parse(out, error);
}
bool Json::as_bool(bool fallback) const {
  return type_ == Type::kBool ? bool_ : fallback;
}
double Json::as_double(double fallback) const {
  return type_ == Type::kNumber ? number_ : fallback;
}
int64_t Json::as_int(int64_t fallback) const {
  return type_ == Type::kNumber ? static_cast<int64_t>(number_) : fallback;
}
std::string Json::as_string(std::string fallback) const {
  return type_ == Type::kString ? string_ : std::move(fallback);
}
const Json* Json::Find(std::string_view key) const {
  if (type_ != Type::kObject) return nullptr;
  const auto it = object_.find(std::string(key));
  return it == object_.end() ? nullptr : &it->second;
}
std::string Json::Dump() const {
  switch (type_) {
    case Type::kNull:
      return "null";
    case Type::kBool:
      return bool_ ? "true" : "false";
    case Type::kNumber:
      return std::to_string(number_);
    case Type::kString:
      return "\"" + string_ + "\"";
    case Type::kArray: {
      std::string out = "[";
      for (size_t i = 0; i < array_.size(); ++i) {
        if (i > 0) out += ",";
        out += array_[i].Dump();
      }
      return out + "]";
    }
    case Type::kObject: {
      std::string out = "{";
      bool first = true;
      for (const auto& [key, value] : object_) {
        if (!first) out += ",";
        first = false;
        out += "\"" + key + "\":" + value.Dump();
      }
      return out + "}";
    }
  }
  return "";
}

// ---------------------------------------------------------------------------
// HTTP and gzip.
// ---------------------------------------------------------------------------
HttpResponse HttpClient::Get(
    const std::string& url,
    const std::vector<std::pair<std::string, std::string>>& headers) const {
  HttpResponse response;
  CURL* curl = curl_easy_init();
  if (curl == nullptr) {
    response.error = "curl initialisation failed";
    return response;
  }
  curl_slist* header_list = nullptr;
  for (const auto& [name, value] : headers) {
    header_list = curl_slist_append(header_list, (name + ": " + value).c_str());
  }
  const auto started = std::chrono::steady_clock::now();
  curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
  curl_easy_setopt(curl, CURLOPT_HTTPHEADER, header_list);
  curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, CurlWrite);
  curl_easy_setopt(curl, CURLOPT_WRITEDATA, &response.body);
  curl_easy_setopt(curl, CURLOPT_TIMEOUT, timeout_seconds_);
  curl_easy_setopt(curl, CURLOPT_CONNECTTIMEOUT, 10L);
  curl_easy_setopt(curl, CURLOPT_FOLLOWLOCATION, 1L);
  curl_easy_setopt(curl, CURLOPT_USERAGENT, "quant/0.1 research");
  const CURLcode code = curl_easy_perform(curl);
  response.elapsed_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                            std::chrono::steady_clock::now() - started)
                            .count();
  if (code != CURLE_OK) {
    response.error = curl_easy_strerror(code);  // Never contains header values.
  } else {
    curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &response.status);
  }
  curl_slist_free_all(header_list);
  curl_easy_cleanup(curl);
  return response;
}

HttpResponse HttpClient::Post(
    const std::string& url,
    const std::vector<std::pair<std::string, std::string>>& headers,
    const std::string& body) const {
  HttpResponse response;
  CURL* curl = curl_easy_init();
  if (curl == nullptr) {
    response.error = "curl initialisation failed";
    return response;
  }
  curl_slist* header_list = nullptr;
  for (const auto& [name, value] : headers) {
    header_list = curl_slist_append(header_list, (name + ": " + value).c_str());
  }
  const auto started = std::chrono::steady_clock::now();
  curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
  curl_easy_setopt(curl, CURLOPT_HTTPHEADER, header_list);
  curl_easy_setopt(curl, CURLOPT_POSTFIELDS, body.c_str());
  curl_easy_setopt(curl, CURLOPT_POSTFIELDSIZE, static_cast<long>(body.size()));
  curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, CurlWrite);
  curl_easy_setopt(curl, CURLOPT_WRITEDATA, &response.body);
  curl_easy_setopt(curl, CURLOPT_TIMEOUT, timeout_seconds_);
  curl_easy_setopt(curl, CURLOPT_CONNECTTIMEOUT, 10L);
  curl_easy_setopt(curl, CURLOPT_FOLLOWLOCATION, 1L);
  curl_easy_setopt(curl, CURLOPT_USERAGENT, "quant/0.1 research");
  const CURLcode code = curl_easy_perform(curl);
  response.elapsed_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                            std::chrono::steady_clock::now() - started)
                            .count();
  if (code != CURLE_OK) {
    response.error = curl_easy_strerror(code);
  } else {
    curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &response.status);
  }
  curl_slist_free_all(header_list);
  curl_easy_cleanup(curl);
  return response;
}

std::vector<uint8_t> Gunzip(const std::vector<uint8_t>& input,
                            std::string* error) {
  std::vector<uint8_t> output;
  z_stream stream{};
  if (inflateInit2(&stream, 15 + 32) != Z_OK) {
    if (error) *error = "zlib initialisation failed";
    return output;
  }
  stream.next_in = const_cast<Bytef*>(input.data());
  stream.avail_in = static_cast<uInt>(input.size());
  std::array<uint8_t, 32768> buffer{};
  int status = Z_OK;
  while (status == Z_OK) {
    stream.next_out = buffer.data();
    stream.avail_out = static_cast<uInt>(buffer.size());
    status = inflate(&stream, Z_NO_FLUSH);
    const size_t produced = buffer.size() - stream.avail_out;
    output.insert(output.end(), buffer.begin(), buffer.begin() + produced);
    if (status == Z_STREAM_END) break;
    if (status != Z_OK && status != Z_BUF_ERROR) break;
  }
  inflateEnd(&stream);
  if (status != Z_STREAM_END && error) {
    *error = "gzip decompression failed";
    return {};
  }
  if (error) error->clear();
  return output;
}

// ---------------------------------------------------------------------------
// Reports and file IO.
// ---------------------------------------------------------------------------
std::string WriteTextFile(const std::string& path, const std::string& content) {
  const size_t slash = path.find_last_of('/');
  if (slash != std::string::npos) {
    std::filesystem::create_directories(path.substr(0, slash));
  }
  std::ofstream output(path);
  if (!output.is_open()) return "cannot write " + path;
  output << content;
  return output.good() ? "" : "write failed for " + path;
}

bool ReadCsvCandles(const std::string& path, std::vector<Candle>* candles,
                    std::string* error) {
  candles->clear();
  std::ifstream input(path);
  if (!input.is_open()) {
    if (error) *error = "cannot open " + path;
    return false;
  }
  std::string line;
  std::getline(input, line);  // header.
  while (std::getline(input, line)) {
    std::vector<std::string> fields;
    std::stringstream ss(line);
    std::string field;
    while (std::getline(ss, field, ',')) fields.push_back(field);
    if (fields.size() < 6) continue;
    Candle candle;
    candle.date = fields[0];
    if (!ParseFloating(fields[1], &candle.open) ||
        !ParseFloating(fields[2], &candle.high) ||
        !ParseFloating(fields[3], &candle.low) ||
        !ParseFloating(fields[4], &candle.close) ||
        !ParseFloating(fields[5], &candle.volume)) {
      if (error) *error = "malformed candle row in " + path;
      return false;
    }
    candles->push_back(candle);
  }
  std::sort(candles->begin(), candles->end(),
            [](const Candle& a, const Candle& b) { return a.date < b.date; });
  return true;
}

std::string RenderReport(const DailyReport& report) {
  std::ostringstream oss;
  oss << "# QUANT daily research report\n\n";
  oss << "- Generated (UTC): " << report.generated_at_utc << "\n";
  oss << "- Market status: " << report.market_status << "\n";
  oss << "- Regime score [ESTIMATED]: " << std::fixed << std::setprecision(3)
      << report.regime << "\n";
  oss << "- Decision: " << report.decision << " — " << report.decision_reason
      << "\n";
  oss << "- Provenance: " << report.data_provenance << "\n\n";
  oss << "## Index observations\n\n";
  for (const std::string& line : report.index_summary)
    oss << "- " << line << "\n";
  oss << "\n## Ranked candidates (if any)\n\n";
  if (report.candidates.empty()) {
    oss << "_No candidate passed all filters._\n\n";
  } else {
    oss << "| Symbol | Last | Spread bps | Score | Qty | Cost INR | Break-even "
           "bps | Strategy |\n";
    oss << "|---|---:|---:|---:|---:|---:|---:|---|\n";
    for (const CandidateReport& c : report.candidates) {
      oss << "| " << c.symbol << " | " << std::setprecision(2) << c.last
          << " | " << std::setprecision(1) << c.spread_bps << " | "
          << std::setprecision(3) << c.score << " | " << c.quantity << " | "
          << std::setprecision(2) << c.total_cost.rupees() << " | "
          << std::setprecision(1) << c.break_even_bps << " | " << c.strategy
          << " |\n";
    }
    oss << "\n";
    for (const CandidateReport& c : report.candidates) {
      oss << "### " << c.symbol << " (" << c.instrument_key << ")\n\n";
      oss << "- Entry: " << c.entry << "\n";
      oss << "- Invalidation: " << c.invalidation << "\n";
      oss << "- Exit: " << c.exit << "\n";
      oss << "- Holding estimate: " << c.holding_sessions << " sessions\n";
      oss << "- Evidence: " << c.rationale << "\n\n";
    }
  }
  oss << "## Rejections\n\n";
  for (const std::string& line : report.rejections) oss << "- " << line << "\n";
  oss << "\n## Labels\n\n";
  oss << "VERIFIED = directly observed; ESTIMATED = modelled; SIMULATED = no "
         "real "
         "orders; INCONCLUSIVE = insufficient evidence; NOT IMPLEMENTED = "
         "absent.\n";
  return oss.str();
}

}  // namespace quant
