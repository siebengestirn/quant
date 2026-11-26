#include "quant.hpp"

#include <zlib.h>

#include <chrono>
#include <cmath>
#include <cstdlib>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <iterator>
#include <sstream>
#include <string>
#include <vector>

namespace quant {
namespace {

int g_failures = 0;
int g_checks = 0;

void Check(bool condition, const std::string& name) {
  ++g_checks;
  if (!condition) {
    ++g_failures;
    std::cout << "FAIL: " << name << "\n";
  }
}

void Near(double actual,
          double expected,
          double tolerance,
          const std::string& name) {
  ++g_checks;
  if (std::abs(actual - expected) > tolerance) {
    ++g_failures;
    std::cout << "FAIL: " << name << " actual=" << actual
              << " expected=" << expected << "\n";
  }
}

std::string TempPath(const std::string& name) {
  return (std::filesystem::temp_directory_path() / ("quant_test_" + name))
      .string();
}

std::vector<Candle> SyntheticCandles(int count,
                                     double start = 100.0,
                                     double drift = 0.002) {
  std::vector<Candle> candles;
  double price = start;
  for (int i = 0; i < count; ++i) {
    price *= 1.0 + drift + 0.005 * std::sin(i * 0.7);
    Candle candle;
    std::time_t day =
        static_cast<std::time_t>(1704067200) + i * 86400;  // 2024-01-01 UTC.
    std::tm tm{};
    gmtime_r(&day, &tm);
    std::ostringstream date_stream;
    date_stream << std::put_time(&tm, "%Y-%m-%d");
    candle.date = date_stream.str();
    candle.open = price * 0.998;
    candle.high = price * 1.01;
    candle.low = price * 0.99;
    candle.close = price;
    candle.volume = 1000000.0 * (1.0 + 0.2 * std::sin(i * 0.3));
    candles.push_back(candle);
  }
  return candles;
}

Quote GoodQuote(double price = 100.0) {
  Quote quote;
  quote.symbol = "TEST";
  quote.last = price;
  quote.bid = price - 0.05;
  quote.ask = price + 0.05;
  quote.bid_qty = 1000;
  quote.ask_qty = 1000;
  const int64_t now = std::chrono::duration_cast<std::chrono::milliseconds>(
                          std::chrono::system_clock::now().time_since_epoch())
                          .count();
  quote.exchange_timestamp_ms = now;
  quote.provider_timestamp_ms = now;
  quote.last_trade_timestamp_ms = now;
  return quote;
}

Signal GoodSignal(const FeatureSet& features) {
  Signal signal;
  signal.symbol = "TEST";
  signal.strategy = "unit_test";
  signal.score = 0.8;
  signal.features = features;
  signal.tradable = true;
  return signal;
}

Config TestConfig() {
  Config config;
  config.initial_capital = Money::FromRupees(500.0);
  config.risk_per_trade_fraction = 0.02;
  config.max_open_positions = 1;
  config.slippage_bps = 10.0;
  config.max_spread_bps = 35.0;
  config.stale_quote_seconds = 300;
  config.max_drawdown_fraction = 0.1;
  config.cash_reserve_fraction = 0.1;
  config.max_gross_exposure_fraction = 0.8;
  return config;
}

void TestCanonicalDateFixture() {
  std::ifstream input("tests/fixtures/canonical_dates.csv");
  Check(input.is_open(), "canonical date fixture opens");
  std::string line;
  std::getline(input, line);  // Header.
  int checked = 0;
  while (std::getline(input, line)) {
    if (line.empty())
      continue;
    const size_t comma = line.find(',');
    if (comma == std::string::npos)
      continue;
    const std::string value = line.substr(0, comma);
    const bool expected = line.substr(comma + 1) == "true";
    Check(IsCanonicalExchangeDate(value) == expected,
          "canonical date fixture: " + value);
    ++checked;
  }
  Check(checked == 14, "all canonical date fixture rows checked");
}

void TestAccountingReplayIntegrity() {
  const std::string path = TempPath("accounting_replay.jnl");
  std::remove(path.c_str());
  std::string error;
  Journal journal;
  Check(journal.Open(path, &error), "replay journal opens");
  CostBreakdown costs;
  costs.status = CalculationStatus::kCalculated;
  costs.brokerage = Money::FromPaise(0);
  costs.stt = Money::FromPaise(100);
  costs.exchange_fee = Money::FromPaise(31);
  costs.gst = Money::FromPaise(24);
  costs.sebi = Money::FromPaise(1);
  costs.stamp = Money::FromPaise(0);
  costs.dp = Money::FromPaise(0);
  costs.slippage = Money::FromPaise(0);
  costs.total = Money::FromPaise(156);
  AccountingPosting posting;
  posting.posting_id = "P-1";
  posting.order_id = "O-1";
  posting.action = "BUY";
  posting.symbol = "AAA";
  posting.quantity = 2;
  posting.price_paise = 10000;
  posting.notional_paise = 20000;
  posting.costs = costs;
  Check(ValidateAccountingPosting(posting, &error), "valid posting accepted");
  Check(AppendAccountingPosting(&journal, posting, &error), "posting appended");
  Check(AppendAccountingPosting(&journal, posting, &error),
        "identical retry appended");
  Portfolio rebuilt(Money::FromRupees(100000.0));
  Check(RebuildPortfolioFromJournal(
            &journal, Money::FromRupees(100000.0), &rebuilt, &error),
        "identical duplicate replays idempotently");
  Check(rebuilt.open_position_count() == 1,
        "idempotent replay posts exactly one position");
  Check(rebuilt.ledger().trade_count == 1, "trade counted once");

  AccountingPosting conflicting = posting;
  conflicting.quantity = 3;
  conflicting.notional_paise = 30000;
  Check(ValidateAccountingPosting(conflicting, &error),
        "conflict fixture valid");
  Check(AppendAccountingPosting(&journal, conflicting, &error),
        "conflicting event appended to journal");
  Portfolio failed(Money::FromRupees(100000.0));
  Check(!RebuildPortfolioFromJournal(
            &journal, Money::FromRupees(100000.0), &failed, &error),
        "conflicting posting ID halts replay");
  Check(std::string(error).find("conflicting") != std::string::npos,
        "conflict failure is explicit");

  const std::string mismatch_path = TempPath("accounting_mismatch.jnl");
  std::remove(mismatch_path.c_str());
  Journal mismatch_journal;
  Check(mismatch_journal.Open(mismatch_path, &error), "mismatch journal opens");
  AccountingPosting mismatched = posting;
  mismatched.posting_id = "P-2";
  mismatched.notional_paise = 99999;
  Check(!ValidateAccountingPosting(mismatched, &error),
        "notional mismatch rejected by shared validator");
  mismatched = posting;
  mismatched.posting_id = "P-3";
  mismatched.costs.total = Money::FromPaise(999);
  Check(!ValidateAccountingPosting(mismatched, &error),
        "cost total mismatch rejected by shared validator");
  Check(mismatch_journal.Replay(&error).empty(),
        "no invalid posting was journalled");
  mismatch_journal.Close();
  journal.Close();
}

void TestMoney() {
  Check(Money::FromRupees(500.0).paise() == 50000,
        "money 500 INR -> 50000 paise");
  Check(Money::FromRupees(0.01).paise() == 1, "money 1 paise");
  Check((Money::FromRupees(1.0) + Money::FromRupees(2.0)).paise() == 300,
        "money addition");
  Check((Money::FromRupees(3.0) - Money::FromRupees(1.0)).paise() == 200,
        "money subtraction");
  Check(Money::FromRupees(1.0) < Money::FromRupees(2.0), "money comparison");
}

void TestConfigParsing() {
  const std::string path = TempPath("config.toml");
  const std::string content =
      "[mode]\nLIVE_TRADING_ENABLED = false\nEXECUTION_MODE = \"PAPER\"\n"
      "ALLOW_MARGIN = false\nALLOW_SHORT_SELLING = false\nALLOW_WITHDRAWALS = "
      "false\n"
      "[capital]\nINITIAL_CAPITAL_INR = 500\nRISK_PER_TRADE_FRACTION = 0.01\n"
      "MAX_OPEN_POSITIONS = 1\nMAX_DRAWDOWN_FRACTION = 0.10\n"
      "[data]\nKEYCHAIN_PREFIX = \"NIRNAY_\"\n"
      "[universe]\nSEED_SYMBOLS = [\"RELIANCE\", \"TCS\"]\nINDEX_SYMBOLS = "
      "[\"NIFTY 50\"]\n";
  WriteTextFile(path, content);
  const ConfigResult result = LoadConfig(path);
  Check(result.ok(), "config loads");
  if (result.ok()) {
    Check(result.config.mode == Mode::kPaper, "config mode parsed");
    Check(result.config.seed_symbols.size() == 2, "config array parsed");
    Check(result.config.initial_capital.paise() == 50000,
          "config capital parsed");
  }
  Config bad = TestConfig();
  bad.allow_margin = true;
  Check(!ValidateConfig(bad).empty(), "config rejects margin");
  bad = TestConfig();
  bad.live_trading_enabled = true;
  Check(!ValidateConfig(bad).empty(), "config rejects unauthorised live");
  Config invalid_numbers = TestConfig();
  invalid_numbers.slippage_bps = std::numeric_limits<double>::quiet_NaN();
  Check(!ValidateConfig(invalid_numbers).empty(), "NaN slippage rejected");
  invalid_numbers = TestConfig();
  invalid_numbers.stop_loss_fraction = 0.0;
  Check(!ValidateConfig(invalid_numbers).empty(), "zero stop rejected");
  invalid_numbers = TestConfig();
  invalid_numbers.queue_capacity = 0;
  Check(!ValidateConfig(invalid_numbers).empty(), "zero queue rejected");
  Config invalid_capital = TestConfig();
  invalid_capital.initial_capital = Money::Invalid();
  Check(!ValidateConfig(invalid_capital).empty(),
        "invalid initial capital rejected");
  Config invalid_sizes = TestConfig();
  invalid_sizes.queue_capacity = (1u << 21);
  Check(!ValidateConfig(invalid_sizes).empty(), "oversized queue rejected");
  invalid_sizes = TestConfig();
  invalid_sizes.http_timeout_seconds = 0;
  Check(!ValidateConfig(invalid_sizes).empty(), "zero HTTP timeout rejected");
  invalid_sizes = TestConfig();
  invalid_sizes.history_days = 0;
  Check(!ValidateConfig(invalid_sizes).empty(), "zero history window rejected");
  invalid_sizes = TestConfig();
  invalid_sizes.kdbx.port = 80;
  Check(!ValidateConfig(invalid_sizes).empty(),
        "non-loopback privileged port rejected");
  Check(ParseMode("LIVE") == Mode::kLive, "mode parse live");
  Check(ModeName(Mode::kDisabled) == "DISABLED", "mode name disabled");
}

void TestCosts() {
  const Money turnover = Money::FromRupees(10000.0);
  const CostBreakdown buy = EstimateCosts(turnover, true, true, 0.0);
  Check(buy.brokerage.paise() == 0, "delivery brokerage zero");
  Near(buy.stt.rupees(), 10.0, 0.01, "delivery STT 0.1% both sides");
  Near(buy.exchange_fee.rupees(), 0.307, 0.01, "NSE transaction charge");
  Near(buy.stamp.rupees(), 1.5, 0.01, "delivery stamp 0.015%");
  const CostBreakdown sell = EstimateCosts(turnover, true, false, 0.0);
  Near(sell.stt.rupees(), 10.0, 0.01, "delivery sell STT");
  Near(sell.dp.rupees(), 15.34, 0.01, "DP charge on delivery sell");
  const CostBreakdown intraday = EstimateCosts(turnover, false, false, 0.0);
  Near(intraday.brokerage.rupees(),
       3.0,
       0.01,
       "intraday brokerage 0.03% below cap");
  const CostBreakdown capped =
      EstimateCosts(Money::FromRupees(100000.0), false, false, 0.0);
  Near(
      capped.brokerage.rupees(), 20.0, 0.01, "intraday brokerage capped at 20");
  Near(intraday.stt.rupees(),
       3.0,
       0.01,
       "intraday sell STT rounds 2.50 up to 3 rupees");
  const CostBreakdown small_delivery =
      EstimateCosts(Money::FromRupees(500.0), true, true, 0.0);
  Near(small_delivery.stt.rupees(),
       1.0,
       0.01,
       "₹500 delivery STT rounds 0.50 up to 1 rupee");
  CostAggregator agg;
  std::string agg_error;
  bool first_dp = false;
  Check(agg.RecognizeExecution("X", "D1", true, false, &first_dp, &agg_error),
        "first execution recognised");
  Check(first_dp, "first delivery sell earns DP entitlement");
  const CostBreakdown committed_first =
      EstimateCosts(Money::FromRupees(1000.0), true, false, 0.0, first_dp);
  Near(committed_first.dp.rupees(),
       15.34,
       0.01,
       "committed first delivery sell incurs DP");
  const CostBreakdown first =
      agg.Estimate(Money::FromRupees(1000.0), true, false, 0.0, "X", "D1");
  const CostBreakdown second =
      agg.Estimate(Money::FromRupees(1000.0), true, false, 0.0, "X", "D1");
  Check(first.dp.paise() == 0 && second.dp.paise() == 0,
        "estimation is pure and never charges DP");
  Check(agg.charged_session_count() == 1,
        "repeated estimation does not mutate settlement state");
  bool second_dp = true;
  Check(agg.RecognizeExecution("X", "D1", true, false, &second_dp, &agg_error),
        "second execution recognised");
  Check(!second_dp, "second same-day sell has no DP charge");
  bool other_symbol_dp = false;
  Check(agg.RecognizeExecution(
            "Y", "D1", true, false, &other_symbol_dp, &agg_error),
        "other scrip recognised");
  Check(other_symbol_dp, "different scrip earns its own DP entitlement");
  Check(intraday.stamp.paise() == 0, "no stamp on intraday sell");
  Check(intraday.dp.paise() == 0, "no DP on intraday sell");
  const CostBreakdown small =
      EstimateCosts(Money::FromRupees(100.0), false, true, 0.0);
  Near(small.brokerage.rupees(), 0.03, 0.001, "intraday brokerage 0.03%");
  const CostBreakdown slipped = EstimateCosts(turnover, false, true, 10.0);
  Near(slipped.slippage.rupees(), 10.0, 0.01, "slippage 10 bps");
  Check(BreakEvenMoveBps(turnover, buy, sell) > 0.0, "break-even positive");
  Check(!FormatCosts(buy).empty(), "cost formatting");
  Check(
      EstimateCosts(Money::FromRupees(0.0), true, true, 0.0).total.paise() == 0,
      "zero turnover costs zero");

  // Explicit calculation status: invalid input is never a valid zero cost.
  const CostBreakdown invalid_turnover =
      EstimateCosts(Money::Invalid(), true, true, 0.0);
  Check(invalid_turnover.status == CalculationStatus::kInvalidInput,
        "invalid turnover reports INVALID_INPUT");
  Check(!invalid_turnover.usable(), "invalid turnover is not usable");
  Check(!invalid_turnover.total.valid(), "invalid turnover has no total");
  Check(EstimateCosts(
            turnover, true, true, std::numeric_limits<double>::quiet_NaN())
                .status == CalculationStatus::kInvalidInput,
        "NaN slippage reports INVALID_INPUT");
  Check(EstimateCosts(Money::FromRupees(-1.0), true, true, 0.0).status ==
            CalculationStatus::kInvalidInput,
        "negative turnover reports INVALID_INPUT");
  Check(EstimateCosts(turnover, true, false, 0.0).status ==
            CalculationStatus::kEstimated,
        "DP component is explicitly ESTIMATED");
  Check(CostBreakdown().status == CalculationStatus::kInvalidInput,
        "default cost breakdown is not a valid zero charge");
  Check(std::string(CalculationStatusName(CalculationStatus::kInvalidInput)) ==
            "INVALID_INPUT",
        "status names are stable");
}

void TestAccounting() {
  Portfolio portfolio(Money::FromRupees(500.0));
  std::string error;
  const Money price = Money::FromRupees(100.0);
  const CostBreakdown entry =
      EstimateCosts(Money::FromPaise(price.paise() * 2), false, true, 0.0);
  Check(portfolio.Buy("X", 2, price, entry, &error), "buy succeeds");
  Check(portfolio.equity() < portfolio.ledger().initial_capital,
        "equity reduced by entry costs");
  Check(portfolio.open_position_count() == 1, "one position open");
  portfolio.MarkToMarket("X", Money::FromRupees(110.0));
  Check(portfolio.ledger().unrealized_pnl.paise() == 2000, "unrealized P&L");
  Check(portfolio.equity() == portfolio.ledger().cash +
                                  portfolio.ledger().invested +
                                  portfolio.ledger().unrealized_pnl,
        "equity identity holds");
  const Money exit_price = Money::FromRupees(110.0);
  const CostBreakdown exit_costs = EstimateCosts(
      Money::FromPaise(exit_price.paise() * 2), false, false, 0.0);
  Check(portfolio.Sell("X", 2, exit_price, exit_costs, &error),
        "sell succeeds");
  Check(portfolio.open_position_count() == 0, "position closed");
  Check(portfolio.ledger().realized_pnl.paise() > 0, "realized P&L positive");
  Check(portfolio.ledger().external_flows.paise() == 0, "no external flows");
  Check(portfolio.equity().rupees() > 500.0, "net profit after costs");

  Portfolio broke(Money::FromRupees(5.0));
  Check(!broke.Buy("X",
                   1,
                   Money::FromRupees(100.0),
                   EstimateCosts(Money::FromRupees(100.0), false, true, 0.0),
                   &error),
        "insufficient funds rejected");
  Portfolio empty(Money::FromRupees(500.0));
  Check(!empty.Sell("X", 1, price, exit_costs, &error), "oversell rejected");

  Portfolio expenses(Money::FromRupees(500.0));
  expenses.RecordExternalFlow(Money::FromRupees(100.0), "test");
  Check(expenses.ledger().external_flows.paise() == 10000,
        "external flow recorded");
  Check(expenses.audit().back().find("RECONCILIATION_EXCEPTION") !=
            std::string::npos,
        "external flow flagged");
  expenses.RecordOperatingExpense(Money::FromRupees(1.0));
  Check(expenses.ledger().operating.paise() == 100,
        "operating expense recorded");

  Portfolio drawdown(Money::FromRupees(500.0));
  drawdown.MarkToMarket("MISSING", Money::FromRupees(1.0));
  Check(drawdown.drawdown_fraction() == 0.0, "no phantom drawdown");
}

void TestRisk() {
  const Config config = TestConfig();
  RiskEngine risk(config);
  const FeatureSet features = ComputeFeatures(SyntheticCandles(150));
  const Signal signal = GoodSignal(features);
  const int64_t now = std::chrono::duration_cast<std::chrono::milliseconds>(
                          std::chrono::system_clock::now().time_since_epoch())
                          .count();
  Portfolio portfolio(config.initial_capital);
  Quote quote = GoodQuote(100.0);
  Check(risk.Evaluate(signal, quote, portfolio, now).approved,
        "valid signal approved");
  Quote stale = quote;
  stale.provider_timestamp_ms = now - 600000;
  Check(!risk.Evaluate(signal, stale, portfolio, now).approved,
        "stale data rejected");
  Quote wide = quote;
  wide.bid = 90.0;
  wide.ask = 110.0;
  Check(!risk.Evaluate(signal, wide, portfolio, now).approved,
        "wide spread rejected");
  Quote no_depth = quote;
  no_depth.bid = 0.0;
  Check(!risk.Evaluate(signal, no_depth, portfolio, now).approved,
        "missing depth rejected");
  Signal not_tradable = signal;
  not_tradable.tradable = false;
  Check(!risk.Evaluate(not_tradable, quote, portfolio, now).approved,
        "upstream rejection respected");

  Portfolio full(config.initial_capital);
  Check(full.Buy("Y",
                 1,
                 Money::FromRupees(100.0),
                 EstimateCosts(Money::FromRupees(100.0), false, true, 0.0),
                 nullptr),
        "seed position for limit test");
  Check(!risk.Evaluate(signal, quote, full, now).approved,
        "max position limit enforced");

  Config big = config;
  big.initial_capital = Money::FromRupees(500.0);
  big.risk_per_trade_fraction = 0.000001;
  Portfolio tiny(Money::FromRupees(500.0));
  const RiskDecision tiny_decision =
      RiskEngine(big).Evaluate(signal, quote, tiny, now);
  Check(!tiny_decision.approved, "sub-one-share risk budget returns NO TRADE");
  Check(tiny_decision.quantity == 0, "no rounded-up quantity");
}

void TestFeatures() {
  const auto candles = SyntheticCandles(180);
  const FeatureSet features = ComputeFeatures(candles);
  Check(features.valid, "features valid with sufficient history");
  Check(features.ret20 > 0.0, "uptrend 20-day return positive");
  Check(features.vol20 > 0.0, "volatility positive");
  Check(features.volume_surprise > -1.0, "volume surprise bounded");
  Check(features.trend >= 0.0 && features.trend <= 1.0, "trend in range");
  Check(features.breakout >= -1.0 && features.breakout <= 1.0,
        "breakout in range");
  Check(!ComputeFeatures(SyntheticCandles(5)).valid, "short history invalid");
  const double momentum = CrossSectionalMomentumScore(features);
  const double trend = TimeSeriesMomentumScore(features);
  const double breakout = VolumeBreakoutScore(features);
  Check(momentum >= -1.0 && momentum <= 1.0, "momentum bounded");
  Check(trend >= -1.0 && trend <= 1.0, "trend score bounded");
  Check(breakout >= -1.0 && breakout <= 1.0, "breakout score bounded");
  Check(EnsembleScore(features, 0.5) >= -1.0, "ensemble lower bound");
  Check(EnsembleScore(features, 0.5) <= 1.0, "ensemble upper bound");
  Check(std::abs(RegimeScore(candles)) <= 1.0, "regime bounded");
  Check(RegimeScore(SyntheticCandles(3)) == 0.0, "short regime zero");
}

void TestJournal() {
  const std::string path = TempPath("journal.jnl");
  std::remove(path.c_str());
  Journal journal;
  std::string error;
  Check(journal.Open(path, &error), "journal opens");
  Check(std::filesystem::exists(path),
        "journal file is created during Open, not deferred to Append");
  for (int i = 0; i < 3; ++i) {
    Event event;
    event.type = EventType::kHeartbeat;
    event.payload = "payload|with|pipes " + std::to_string(i);
    event.timestamp_ms = 1000 + i;
    Check(journal.Append(&event, &error), "journal append");
  }
  const auto events = journal.Replay(&error);
  Check(error.empty(), "journal replay clean");
  Check(events.size() == 3, "journal event count");
  Check(events[1].payload == "payload|with|pipes 1",
        "journal payload roundtrip");
  Check(events[0].sequence == 1 && events[2].sequence == 3,
        "journal sequences");
  journal.Close();
  Journal reopened;
  reopened.Open(path, &error);
  Check(reopened.next_sequence() == 4, "journal resumes sequence");

  std::ifstream input(path);
  std::string line;
  std::getline(input, line);
  input.close();
  auto pos = line.rfind('|');
  std::string corrupted = line.substr(0, pos + 1) + "deadbeef";
  std::ofstream output(path);
  output << corrupted << "\n";
  output.close();
  reopened.Close();
  Journal broken;
  Check(!broken.Open(path, &error), "journal open rejects corrupt history");
  const auto replayed = broken.Replay(&error);
  Check(replayed.empty() && !error.empty(), "checksum mismatch detected");
  Check(Checksum("abc") != Checksum("abd"), "checksum differs");

  const std::string lock_path = TempPath("journal_lock.jnl");
  std::remove(lock_path.c_str());
  Journal first_writer;
  Check(first_writer.Open(lock_path, &error), "first writer acquires lock");
  Journal second_writer;
  Check(!second_writer.Open(lock_path, &error),
        "second writer is rejected by exclusive lock");
  first_writer.Close();

  const std::string tail_path = TempPath("journal_tail.jnl");
  std::remove(tail_path.c_str());
  Journal tail;
  Check(tail.Open(tail_path, &error), "tail journal opens");
  Event tail_event;
  tail_event.type = EventType::kHeartbeat;
  tail_event.payload = "tail";
  tail_event.timestamp_ms = 2000;
  Check(tail.Append(&tail_event, &error), "tail journal append");
  std::ifstream tail_input(tail_path, std::ios::binary);
  std::string complete((std::istreambuf_iterator<char>(tail_input)),
                       std::istreambuf_iterator<char>());
  tail_input.close();
  complete.pop_back();  // Simulate abrupt termination before the newline.
  std::ofstream tail_output(tail_path, std::ios::binary | std::ios::trunc);
  tail_output << complete;
  tail_output.close();
  tail.Close();
  Journal truncated;
  Check(!truncated.Open(tail_path, &error),
        "truncated journal is rejected before append");
  Check(error.find("incomplete trailing journal record") != std::string::npos,
        "incomplete trailing record is explicit");

  // Exclusive writer ownership: a closed object must not append after another
  // instance has acquired the journal lock.
  const std::string owner_path = TempPath("journal_owner.jnl");
  std::remove(owner_path.c_str());
  Journal writer_a;
  Check(writer_a.Open(owner_path, &error), "writer A opens");
  Event owned;
  owned.type = EventType::kHeartbeat;
  owned.payload = "owned";
  owned.timestamp_ms = 3000;
  Check(writer_a.Append(&owned, &error), "writer A appends");
  writer_a.Close();
  Journal writer_b;
  Check(writer_b.Open(owner_path, &error), "writer B acquires the lock");
  Event rejected;
  rejected.type = EventType::kHeartbeat;
  rejected.payload = "rejected";
  rejected.timestamp_ms = 4000;
  Check(!writer_a.Append(&rejected, &error),
        "closed writer cannot append while another owns the lock");
  Check(writer_b.Append(&owned, &error), "current writer can append");
  writer_b.Close();

  // Append and Replay enforce one identical event contract.
  const std::string schema_path = TempPath("journal_schema.jnl");
  std::remove(schema_path.c_str());
  Journal schema_journal;
  Check(schema_journal.Open(schema_path, &error), "schema journal opens");
  Check(!schema_journal.Open(schema_path, &error), "repeated open is rejected");
  Event bad_schema;
  bad_schema.type = EventType::kHeartbeat;
  bad_schema.schema_version = 999;
  bad_schema.payload = "{}";
  bad_schema.timestamp_ms = 9000;
  const uint64_t before = schema_journal.next_sequence();
  Check(!schema_journal.Append(&bad_schema, &error),
        "unsupported schema is rejected at append");
  Check(bad_schema.sequence == 0 && bad_schema.id.empty() &&
            bad_schema.checksum.empty(),
        "rejected append does not mutate the caller event");
  Check(std::string(error).find("schema") != std::string::npos,
        "schema rejection is explicit");
  Check(schema_journal.healthy(), "rejection leaves journal healthy");
  Check(schema_journal.next_sequence() == before,
        "rejection does not advance durable sequence");
  Event bad_type;
  bad_type.type = static_cast<EventType>(77);
  bad_type.timestamp_ms = 9001;
  Check(!schema_journal.Append(&bad_type, &error),
        "invalid event type rejected at append");
  Event bad_id;
  bad_id.type = EventType::kHeartbeat;
  bad_id.id = "bad|id";
  bad_id.timestamp_ms = 9002;
  Check(!schema_journal.Append(&bad_id, &error),
        "delimiter in event id rejected at append");
  Event good_event;
  good_event.type = EventType::kHeartbeat;
  good_event.payload = "{}";
  good_event.timestamp_ms = 9003;
  Check(schema_journal.Append(&good_event, &error),
        "valid event appends after rejections");
  schema_journal.Close();
  Journal schema_reopen;
  Check(schema_reopen.Open(schema_path, &error),
        "journal containing only valid events reopens");
  Check(error.empty(), "reopen error is empty");
  Check(schema_reopen.Replay(&error).size() == 1,
        "replay contains exactly the committed event");
  schema_reopen.Close();
}

void TestJson() {
  Json root;
  std::string error;
  const std::string text =
      "{\"status\":\"success\",\"data\":{\"n\":42,\"f\":1.5,"
      "\"b\":true,\"z\":null,\"a\":[1,2,{\"x\":\"y\"}]}}";
  Check(Json::Parse(text, &root, &error), "json parses");
  Check(error.empty(), "json no error");
  Check(root.Find("status")->as_string() == "success", "json string field");
  const Json* data = root.Find("data");
  Check(data != nullptr, "json nested object");
  Check(data->Find("n")->as_int(0) == 42, "json int field");
  Near(data->Find("f")->as_double(0.0), 1.5, 1e-9, "json double field");
  Check(data->Find("b")->as_bool(false), "json bool field");
  Check(data->Find("z")->is_null(), "json null field");
  Check(data->Find("a")->array().size() == 3, "json array size");
  Check(data->Find("a")->array()[2].Find("x")->as_string() == "y",
        "json nested array object");
  Check(!Json::Parse("{\"a\":}", &root, &error), "json malformed rejected");
}

void TestNormalization() {
  Json item;
  std::string error;
  Check(Json::Parse(
            "{\"last_price\":100.5,\"volume\":1234,"
            "\"timestamp\":1795400000000,\"last_trade_time\":1795400000000,"
            "\"ohlc\":{\"open\":99,\"high\":101,\"low\":98,\"close\":100},"
            "\"depth\":{\"buy\":[{\"price\":100.4,\"quantity\":500}],"
            "\"sell\":[{\"price\":100.6,\"quantity\":400}]}}",
            &item,
            &error),
        "quote json parses");
  const Quote quote = NormalizeUpstoxQuote(item, "X", "NSE_EQ|ABC");
  Near(quote.last, 100.5, 1e-9, "normalised last price");
  Check(quote.bid_qty == 500 && quote.ask_qty == 400, "normalised depth");
  Near(SpreadBps(quote), 19.9, 0.1, "spread bps");
  Check(quote.quality == "ok", "quote quality ok");
  Check(!IsStale(quote, 1795400000000, 300), "fresh quote");
  Check(IsStale(quote, 1795400300001, 300), "stale quote");
  Quote empty;
  Check(!std::isfinite(SpreadBps(empty)), "missing spread infinite");

  std::vector<Instrument> instruments(3);
  instruments[0].symbol = "OK";
  instruments[0].security_type = "NORMAL";
  instruments[0].instrument_class = InstrumentClass::kEquity;
  instruments[0].tick_size = 0.05;
  instruments[0].isin = "INE1";
  instruments[1].symbol = "SUSP";
  instruments[1].security_type = "NORMAL";
  instruments[1].instrument_class = InstrumentClass::kEquity;
  instruments[1].tick_size = 0.05;
  instruments[1].isin = "INE2";
  instruments[2].symbol = "SME";
  instruments[2].security_type = "SME";
  instruments[2].instrument_class = InstrumentClass::kSme;
  instruments[2].tick_size = 0.05;
  std::vector<Instrument> suspended(1);
  suspended[0].isin = "INE2";
  const auto result =
      ApplyEligibility(instruments, suspended, true, TestConfig());
  Check(result[0].eligible, "normal instrument eligible");
  Check(!result[1].eligible, "suspended instrument excluded");
  Check(!result[2].eligible, "SME excluded");
  const auto unknown_status =
      ApplyEligibility(instruments, suspended, false, TestConfig());
  Check(unknown_status[0].eligibility_reason.find(
            "INELIGIBLE_PENDING_VERIFICATION") != std::string::npos,
        "missing surveillance data fails closed");
  Check(!unknown_status[0].eligible, "unknown status is not tradable");
  Instrument index;
  index.symbol = "NIFTYBANK";
  index.instrument_class = InstrumentClass::kIndex;
  index.security_type = "NORMAL";
  index.tick_size = 0.05;
  const auto with_index =
      ApplyEligibility(std::vector<Instrument>{index}, {}, true, TestConfig());
  Check(!with_index[0].eligible, "index instrument is not ordinary equity");
}

void TestOrders() {
  OrderStateMachine machine;
  Check(machine.state() == OrderState::kNew, "order starts new");
  Check(machine.Transition(OrderState::kValidated), "new -> validated");
  Check(machine.Transition(OrderState::kSent), "validated -> sent");
  Check(machine.Transition(OrderState::kAmbiguous), "sent -> ambiguous");
  Check(!machine.Transition(OrderState::kFilled),
        "ambiguous cannot auto-resolve to filled");
  Check(!machine.Transition(OrderState::kCancelled),
        "ambiguous cannot auto-cancel");
  OrderStateMachine other;
  Check(!other.Transition(OrderState::kFilled), "new cannot jump to filled");

  const std::string path = TempPath("order.jnl");
  std::remove(path.c_str());
  Journal journal;
  journal.Open(path, nullptr);
  ZerodhaGateway gateway(TestConfig(), &journal);
  OrderRequest request;
  request.symbol = "TEST";
  request.quantity = 1;
  request.client_order_id = "id-1";
  const OrderResult result = gateway.Submit(request);
  Check(!result.accepted && result.status == "LOCKED",
        "live order hard locked");
  Check(!gateway.ComplianceGate().empty(), "compliance gate text present");
  std::string error;
  const auto events = journal.Replay(&error);
  Check(events.size() == 1 && events[0].type == EventType::kOrder,
        "blocked order attempt journaled");
}

void TestBacktest() {
  std::map<std::string, std::vector<Candle>> history;
  history["NIFTY 50"] = SyntheticCandles(200, 20000.0, 0.001);
  history["AAA"] = SyntheticCandles(200, 100.0, 0.003);
  history["BBB"] = SyntheticCandles(200, 200.0, -0.001);
  const BacktestMetrics first = RunBacktest(history, TestConfig());
  const BacktestMetrics second = RunBacktest(history, TestConfig());
  Check(first.trades == second.trades, "backtest deterministic trades");
  Check(first.net_pnl == second.net_pnl, "backtest deterministic pnl");
  Check(first.final_equity > 0.0, "backtest equity positive");
  std::map<std::string, std::vector<Candle>> short_history;
  short_history["AAA"] = SyntheticCandles(10);
  Check(RunBacktest(short_history, TestConfig()).trades == 0,
        "insufficient history yields no trades");
  const auto ci =
      BootstrapSharpeCI({0.01, -0.01, 0.02, -0.005, 0.003}, 42, 100);
  Check(ci[0] <= ci[1] && ci[1] <= ci[2], "bootstrap CI ordered");
  Check(DeflatedSharpe(2.0, 8, 200) < 2.0, "deflated sharpe haircut applied");
  Check(DeflatedSharpe(2.0, 1, 3) <= 2.0, "deflated sharpe bounded");
}

std::vector<uint8_t> GzipCompress(const std::string& input) {
  z_stream stream{};
  deflateInit2(&stream,
               Z_DEFAULT_COMPRESSION,
               Z_DEFLATED,
               15 + 16,
               8,
               Z_DEFAULT_STRATEGY);
  stream.next_in = reinterpret_cast<Bytef*>(const_cast<char*>(input.data()));
  stream.avail_in = static_cast<uInt>(input.size());
  std::vector<uint8_t> out(32768);
  stream.next_out = out.data();
  stream.avail_out = static_cast<uInt>(out.size());
  deflate(&stream, Z_FINISH);
  const size_t size = out.size() - stream.avail_out;
  deflateEnd(&stream);
  out.resize(size);
  return out;
}

void TestGunzip() {
  std::string error;
  const std::vector<uint8_t> compressed = GzipCompress("hello quant evidence");
  const std::vector<uint8_t> decoded = Gunzip(compressed, &error);
  Check(error.empty(), "gunzip no error");
  Check(std::string(decoded.begin(), decoded.end()) == "hello quant evidence",
        "gunzip roundtrip");
  std::vector<uint8_t> bad = compressed;
  bad[0] = 0x00;
  Check(Gunzip(bad, &error).empty(), "invalid gzip rejected");
}

void TestDailyRiskLimit() {
  Config config = TestConfig();
  config.max_daily_loss_fraction = 0.02;
  auto state = std::make_shared<DailyRiskState>();
  RiskEngine first(config, state);
  Signal signal;
  signal.tradable = true;
  Portfolio opening(Money::FromRupees(1000.0));
  const int64_t now = 1795400000000;  // 2026-11-23 UTC.
  Check(first.Evaluate(signal, GoodQuote(50.0), opening, now).approved,
        "daily risk baseline established");
  Check(state->starting_equity.paise() == 100000,
        "daily starting equity recorded");

  Portfolio depleted(Money::FromRupees(970.0));  // 3% daily loss.
  Portfolio same_day(Money::FromRupees(970.0));
  RiskEngine restart(config, state);
  const RiskDecision breach =
      restart.Evaluate(signal, GoodQuote(50.0), same_day, now);
  Check(
      !breach.approved && breach.reason.find("daily loss") != std::string::npos,
      "daily loss limit rejects new decisions");
  Check(state->halted, "daily kill switch latches");

  RiskEngine next_day(config, state);
  Portfolio fresh(Money::FromRupees(970.0));
  Check(next_day.Evaluate(signal, GoodQuote(50.0), fresh, now + 86400000)
            .approved,
        "new session resets daily baseline");
  (void)depleted;
}

void TestFreshnessSemantics() {
  const int64_t now = 1795400000000;
  Quote quote;
  quote.provider_timestamp_ms = now;
  quote.last_trade_timestamp_ms = now - 600000;
  Check(!IsStaleProviderUpdate(quote, now, 300),
        "fresh provider update with old trade is fresh");
  Check(IsStaleTrade(quote, now, 300), "old last trade is stale for trades");

  quote.provider_timestamp_ms = now - 600000;
  quote.last_trade_timestamp_ms = now;
  Check(IsStaleProviderUpdate(quote, now, 300),
        "old provider update is stale even with recent trade");
  Check(!IsStaleTrade(quote, now, 300), "recent trade is fresh for trades");

  quote.provider_timestamp_ms = 0;
  Check(ProviderFreshnessUnknown(quote), "missing provider time is unknown");
  quote.last_trade_timestamp_ms = 0;
  Check(TradeFreshnessUnknown(quote), "missing last trade time is unknown");

  Config config = TestConfig();
  Portfolio portfolio(config.initial_capital);
  Signal signal;
  signal.tradable = true;
  Quote unknown = GoodQuote();
  unknown.provider_timestamp_ms = 0;
  const int64_t risk_now =
      std::chrono::duration_cast<std::chrono::milliseconds>(
          std::chrono::system_clock::now().time_since_epoch())
          .count();
  const RiskDecision decision =
      RiskEngine(config).Evaluate(signal, unknown, portfolio, risk_now);
  Check(!decision.approved &&
            decision.reason.find("freshness unknown") != std::string::npos,
        "risk rejects unknown feed freshness");
}

void TestMarkedSaleAndCostBoundaries() {
  Portfolio portfolio(Money::FromRupees(500.0));
  std::string error;
  CostBreakdown zero;
  zero.status = CalculationStatus::kCalculated;
  Check(portfolio.Buy("Z", 1, Money::FromRupees(100.0), zero, &error),
        "zero-cost buy");
  Check(portfolio.MarkToMarket("Z", Money::FromRupees(120.0), &error),
        "zero-cost mark");
  Near(portfolio.equity().rupees(), 520.0, 0.01, "marked equity before sale");
  Check(portfolio.Sell("Z", 1, Money::FromRupees(120.0), zero, &error),
        "zero-cost sale");
  Near(portfolio.ledger().cash.rupees(), 520.0, 0.01, "cash after sale");
  Near(portfolio.ledger().invested.rupees(),
       0.0,
       0.01,
       "cost basis after full sale");
  Near(portfolio.ledger().unrealized_pnl.rupees(),
       0.0,
       0.01,
       "unrealized P&L after full sale");
  Near(portfolio.equity().rupees(), 520.0, 0.01, "equity after full sale");
  Near(portfolio.ledger().peak_equity.rupees(),
       520.0,
       0.01,
       "peak after full sale");

  Near(EstimateCosts(Money::FromRupees(10.0), true, true, 0.0).stt.rupees(),
       0.0,
       0.01,
       "raw STT 0.01 rounds to zero");
  Near(EstimateCosts(Money::FromRupees(490.0), true, true, 0.0).stt.rupees(),
       0.0,
       0.01,
       "raw STT 0.49 rounds to zero");
  Near(EstimateCosts(Money::FromRupees(500.0), true, true, 0.0).stt.rupees(),
       1.0,
       0.01,
       "raw STT 0.50 rounds up");
  Near(EstimateCosts(Money::FromRupees(1400.0), true, true, 0.0).stt.rupees(),
       1.0,
       0.01,
       "raw STT 1.40 rounds down");
  Near(EstimateCosts(Money::FromRupees(1500.0), true, true, 0.0).stt.rupees(),
       2.0,
       0.01,
       "raw STT 1.50 rounds up");
  // Exact integer-rational boundaries at large notional (no FP drift).
  Near(EstimateCosts(Money::FromPaise(49999), true, true, 0.0).stt.rupees(),
       0.0,
       0.0,
       "delivery STT 0.49999 rounds to zero rupees");
  Near(EstimateCosts(Money::FromPaise(50000), true, true, 0.0).stt.rupees(),
       1.0,
       0.0,
       "delivery STT 0.50 rounds up to one rupee");
  Near(EstimateCosts(Money::FromPaise(149000), true, true, 0.0).stt.rupees(),
       1.0,
       0.0,
       "delivery STT 1.49 rounds to one rupee");
  Near(EstimateCosts(Money::FromPaise(199999), false, false, 0.0).stt.rupees(),
       0.0,
       0.0,
       "intraday sell STT 0.4999975 rounds to zero");
  Near(EstimateCosts(Money::FromPaise(200000), false, false, 0.0).stt.rupees(),
       1.0,
       0.0,
       "intraday sell STT 0.50 rounds up to one rupee");

  Config config = TestConfig();
  SizingInput missing;
  missing.price = Money::FromRupees(100.0);
  missing.stop_distance = 0.05;
  missing.available_depth = 0;
  const SizingResult rejected = SizePosition(config, portfolio, missing);
  Check(rejected.quantity == 0 &&
            rejected.reason.find("depth") != std::string::npos,
        "missing depth rejects execution sizing");
  missing.capacity_estimate = true;
  const SizingResult capacity = SizePosition(config, portfolio, missing);
  Check(capacity.quantity > 0, "historical capacity mode allowed explicitly");

  Check(!Money::FromRupees(std::numeric_limits<double>::quiet_NaN()).valid(),
        "invalid conversion does not produce valid money");
}

void TestHighWaterAndMoneyBounds() {
  Portfolio portfolio(Money::FromRupees(500.0));
  std::string error;
  Check(portfolio.Buy("X",
                      1,
                      Money::FromRupees(100.0),
                      EstimateCosts(Money::FromRupees(100.0), false, true, 0.0),
                      &error),
        "high-water fixture buy");
  Check(portfolio.MarkToMarket("X", Money::FromRupees(120.0), &error),
        "high-water fixture mark");
  const Money peak_before = portfolio.ledger().peak_equity;
  Check(
      portfolio.Sell("X",
                     1,
                     Money::FromRupees(120.0),
                     EstimateCosts(Money::FromRupees(120.0), false, false, 0.0),
                     &error),
      "high-water fixture profitable close");
  Check(portfolio.ledger().peak_equity >= peak_before,
        "profitable close does not leave peak below equity");
  Check(portfolio.drawdown().paise() >= 0, "drawdown is never negative");

  Money parsed;
  Check(Money::TryFromRupees(123.45, &parsed) && parsed.paise() == 12345,
        "checked rupee conversion");
  Check(
      !Money::TryFromRupees(std::numeric_limits<double>::quiet_NaN(), &parsed),
      "NaN money rejected");
  Check(!Money::TryFromRupees(std::numeric_limits<double>::infinity(), &parsed),
        "infinite money rejected");
  Check(!Money::TryFromRupees(1e20, &parsed), "out-of-range money rejected");
}

void TestDynamicSizing() {
  const Config config = TestConfig();
  SizingInput input;
  input.price = Money::FromRupees(100.0);
  input.volatility = 0.02;
  input.available_depth = 1000;

  Portfolio small(config.initial_capital);
  const SizingResult small_result = SizePosition(config, small, input);
  Check(small_result.quantity >= 1, "₹500 portfolio can size one share");

  Portfolio large(Money::FromRupees(5000.0));
  const SizingResult large_result = SizePosition(config, large, input);
  Check(large_result.quantity > small_result.quantity,
        "sizing uses current equity rather than initial capital");

  Candle down;
  down.date = "2026-11-25";
  down.open = 100.0;
  down.high = 100.0;
  down.low = 80.0;
  down.close = 80.0;
  Check(large.Buy("DD",
                  10,
                  Money::FromRupees(100.0),
                  EstimateCosts(Money::FromRupees(1000.0), false, true, 0.0),
                  nullptr),
        "drawdown fixture position opened");
  large.MarkToMarket("DD", Money::FromRupees(80.0));
  const SizingResult after_drawdown = SizePosition(config, large, input);
  Check(after_drawdown.quantity <= large_result.quantity,
        "drawdown reduces position size");

  Portfolio broke(Money::FromRupees(60.0));
  const SizingResult broke_result = SizePosition(config, broke, input);
  Check(broke_result.quantity == 0, "insufficient cash returns NO TRADE");
  Check(broke_result.reason.find("budget") != std::string::npos,
        "NO TRADE reason is explicit");
}

void TestSizingConstraints() {
  const Config config = TestConfig();
  Portfolio rich(Money::FromRupees(100000.0));
  SizingInput input;
  input.price = Money::FromRupees(80.0);  // Executable ask, not LTP.
  input.stop_distance = 0.05;
  input.available_depth = 500;
  input.current_exposure = Money::FromRupees(0.0);
  const SizingResult deep = SizePosition(config, rich, input);
  Check(deep.quantity == 5, "ask depth participation caps quantity");

  input.available_depth = 100000;
  const SizingResult unrestricted = SizePosition(config, rich, input);
  Check(unrestricted.quantity > deep.quantity,
        "larger executable depth permits more quantity");
  Check(unrestricted.notional.paise() ==
            input.price.paise() * unrestricted.quantity,
        "notional uses intended execution price");

  input.stop_distance = 0.50;
  const SizingResult wide_stop = SizePosition(config, rich, input);
  Check(wide_stop.quantity < unrestricted.quantity,
        "stop distance controls risk-based quantity");

  Portfolio poor(Money::FromRupees(95.0));
  SizingInput reserve_input;
  reserve_input.price = Money::FromRupees(90.0);
  reserve_input.stop_distance = 0.05;
  reserve_input.available_depth = 1000;
  const SizingResult reserved = SizePosition(config, poor, reserve_input);
  Check(reserved.quantity == 0, "cash reserve is preserved after costs");

  SizingInput tick_input = input;
  tick_input.price = Money::FromRupees(80.01);
  tick_input.tick_size = 1.0;
  const SizingResult bad_tick = SizePosition(config, rich, tick_input);
  Check(bad_tick.quantity == 0, "tick-size violation is rejected");

  Portfolio exposed(Money::FromRupees(100000.0));
  SizingInput exposure_input = input;
  exposure_input.available_depth = 100000;
  exposure_input.current_exposure = Money::FromRupees(79900.0);
  const SizingResult capped = SizePosition(config, exposed, exposure_input);
  Check(capped.quantity <= 1, "existing exposure reduces allowance");
}

void TestExecutionSemantics() {
  Candle gap_stop;
  gap_stop.date = "2026-11-25";
  gap_stop.open = 100.0;
  gap_stop.high = 101.0;
  gap_stop.low = 90.0;
  gap_stop.close = 99.0;
  Check(ConservativeStopFill(100.0, gap_stop).paise() == 9500,
        "stop crossing with recovery close fills at stop, not open");

  Candle gap_down;
  gap_down.date = "2026-11-26";
  gap_down.open = 90.0;
  gap_down.high = 92.0;
  gap_down.low = 85.0;
  gap_down.close = 88.0;
  Check(ConservativeStopFill(100.0, gap_down).paise() == 9000,
        "gap-down stop fills at the open");

  std::vector<Candle> series;
  for (const char* date : {"2026-01-01", "2026-01-02", "2026-01-05"}) {
    Candle candle;
    candle.date = date;
    candle.open = 100.0;
    candle.high = 101.0;
    candle.low = 99.0;
    candle.close = 100.0;
    candle.volume = 1000.0;
    series.push_back(candle);
  }
  Check(NextSessionIndex(series, "2026-01-01") == 1,
        "next session uses the instrument's own calendar");
  Check(NextSessionIndex(series, "2026-01-02") == 2,
        "weekend gap is skipped correctly");
  Check(NextSessionIndex(series, "2026-01-05") == series.size(),
        "no session after final date");

  std::vector<Candle> volumes;
  for (int i = 0; i < 30; ++i) {
    Candle candle;
    candle.date = "2026-02-" + std::to_string(i + 1);
    candle.volume = 1000.0;
    volumes.push_back(candle);
  }
  volumes.push_back(volumes[29]);
  volumes[30].volume = 100000000.0;  // Huge volume on the execution day.
  Check(PriorAverageVolume(volumes, 30, 20) == 1000.0,
        "execution-day volume does not leak into the decision");
}

void TestKdbxGate() {
  const KdbxStatus status = KdbxClient::Detect();
  Check(!status.detail.empty(), "kdbx detect returns a status");
  Check(status.detail.size() > 0, "kdbx status explains availability");
  KdbxConfig disabled = KdbxConfig();
  disabled.enabled = false;
  KdbxClient client(disabled);
  std::string error;
  Check(!client.Connect(&error), "disabled kdbx client cannot connect");
  Check(!error.empty(), "disabled kdbx reports why");
  std::string output;
  Check(!client.QueryJson(".quant.countsJson", &output, &error),
        "kdbx query fails closed when disabled");
  Check(!client.IngestQuote(Quote(), 1, "x", &error),
        "kdbx write fails closed when disabled");
  Check(!client.QueryJson("1+1", &output, &error),
        "arbitrary q expressions are not exposed");
}

void TestCsv() {
  const std::string path = TempPath("candles.csv");
  WriteTextFile(path,
                "date,open,high,low,close,volume\n"
                "2025-01-03,103,105,102,104,1000\n"
                "2025-01-02,101,104,100,103,900\n");
  std::vector<Candle> candles;
  std::string error;
  Check(ReadCsvCandles(path, &candles, &error), "csv reads");
  Check(candles.size() == 2, "csv row count");
  Check(candles[0].date < candles[1].date, "csv sorted by date");
  Near(candles[0].close, 103.0, 1e-9, "csv close value");
}

}  // namespace
}  // namespace quant

using namespace quant;

RiskSnapshot SnapshotFromState(const DailyRiskState& state,
                               const std::string& journal_path) {
  RiskSnapshot snapshot;
  snapshot.account_id = "PRIMARY";
  snapshot.session_id = state.date;
  snapshot.policy_version = state.policy_version;
  snapshot.journal_sequence = state.journal_sequence;
  snapshot.starting_equity_paise = state.starting_equity.paise();
  snapshot.current_equity_paise = state.current_equity.paise();
  snapshot.peak_equity_paise = state.peak_equity.paise();
  snapshot.low_equity_paise = state.low_equity.paise();
  snapshot.realized_pnl_paise = state.realized_pnl.paise();
  snapshot.unrealized_pnl_paise = state.unrealized_pnl.paise();
  snapshot.halted = state.halted;
  snapshot.halt_reason = state.halt_reason;
  std::string identity_error;
  Journal identity_journal;
  if (identity_journal.Open(journal_path, &identity_error)) {
    const auto events = identity_journal.Replay(&identity_error);
    identity_journal.Close();
    FillRiskCheckpointIdentity(&snapshot, events, &identity_error);
  }
  return snapshot;
}

class RecordingPublisher : public MarketDataPublisher {
 public:
  bool PublishQuote(const Quote& quote,
                    uint64_t sequence,
                    const std::string& event_id,
                    std::string* error) override {
    if (fail_after >= 0 && quotes >= static_cast<size_t>(fail_after)) {
      if (error)
        *error = "injected publisher failure";
      return false;
    }
    ++quotes;
    last_sequence = sequence;
    last_event_id = event_id;
    last_symbol = quote.symbol;
    return true;
  }
  bool PublishBar(const Candle& candle,
                  const std::string& symbol,
                  std::string* error) override {
    if (fail_after >= 0 && bars >= static_cast<size_t>(fail_after)) {
      if (error)
        *error = "injected publisher failure";
      return false;
    }
    ++bars;
    last_symbol = symbol;
    last_close = candle.close;
    return true;
  }
  size_t quotes = 0;
  size_t bars = 0;
  int fail_after = -1;
  uint64_t last_sequence = 0;
  std::string last_event_id;
  std::string last_symbol;
  double last_close = 0.0;
};

void TestMarketDataPipeline() {
  const std::string path = TempPath("market_pipeline.jnl");
  std::remove(path.c_str());
  std::string error;
  Journal journal;
  Check(journal.Open(path, &error), "pipeline journal opens");
  MarketDataPipeline pipeline(&journal);

  Quote invalid = GoodQuote(100.0);
  invalid.bid = 0.0;
  Check(!pipeline.IngestQuote(invalid, &error),
        "canonical quote rejects missing depth");
  Quote quote = GoodQuote(100.0);
  quote.symbol = "AAA";
  quote.provider_key = "UPSTOX:AAA";
  quote.exchange_timestamp_ms = 1000;
  quote.provider_timestamp_ms = 1001;
  quote.receive_timestamp_ms = 1002;
  Check(pipeline.IngestQuote(quote, &error), "canonical quote journalled");
  Quote bad_volume = quote;
  bad_volume.volume = std::numeric_limits<double>::quiet_NaN();
  Check(!pipeline.IngestQuote(bad_volume, &error),
        "non-finite quote volume rejected");
  Candle candle;
  candle.date = "2026-11-26";
  candle.open = 100.0;
  candle.high = 102.0;
  candle.low = 99.0;
  candle.close = 101.0;
  candle.volume = 1000.0;
  Check(pipeline.IngestBar(candle, "AAA", &error), "canonical bar journalled");
  Candle bad_open = candle;
  bad_open.open = 90.0;  // below low of 99.
  Check(!pipeline.IngestBar(bad_open, "AAA", &error),
        "open outside high/low range rejected");
  Candle bad_date = candle;
  bad_date.date = "26/11/2026";
  Check(!pipeline.IngestBar(bad_date, "AAA", &error),
        "non-exchange date rejected");
  Candle impossible_date = candle;
  impossible_date.date = "2026-02-31";
  Check(!pipeline.IngestBar(impossible_date, "AAA", &error),
        "impossible calendar date rejected");
  Candle leap_date = candle;
  leap_date.date = "2024-02-29";
  Check(pipeline.IngestBar(leap_date, "AAA", &error), "leap day accepted");

  Check(journal.Replay(&error).size() == 3,
        "journal holds exactly the committed market events");

  KdbxCheckpoint checkpoint;
  checkpoint.journal_watermark = 0;
  RecordingPublisher publisher;
  const PipelineResult first = pipeline.Publish(checkpoint, &publisher, &error);
  Check(first.status == PipelineStatus::kComplete,
        "pipeline publishes committed events");
  Check(publisher.quotes == 1 && publisher.bars == 2,
        "one quote and two bars projected");
  Check(publisher.last_sequence == 1 && !publisher.last_event_id.empty(),
        "event identity and sequence survive projection");

  checkpoint.journal_watermark = 3;
  RecordingPublisher second_publisher;
  const PipelineResult second =
      pipeline.Publish(checkpoint, &second_publisher, &error);
  Check(second.status == PipelineStatus::kComplete && second.published == 0,
        "checkpoint watermark prevents duplicate publication");

  RecordingPublisher failing;
  failing.fail_after = 0;
  const PipelineResult failed =
      pipeline.Publish(KdbxCheckpoint(), &failing, &error);
  Check(failed.status == PipelineStatus::kFailed,
        "publisher failure propagates as FAILED");
  Check(std::string(PipelineStatusName(PipelineStatus::kFailed)) == "FAILED",
        "pipeline status names are stable");
  journal.Close();
}

void TestPaperPartialAndReplay() {
  const std::string path = TempPath("paper_partial.jnl");
  std::remove(path.c_str());
  std::string error;
  Journal journal;
  Check(journal.Open(path, &error), "partial paper journal opens");
  auto portfolio = std::make_shared<Portfolio>(Money::FromRupees(100000.0));
  Config config = TestConfig();
  config.max_participation_fraction = 0.5;
  PaperBroker broker(config, &journal, portfolio);

  PaperOrder* order = broker.CreateIntent(
      "AAA", OrderSide::kBuy, 4, Money::FromRupees(200.0), &error);
  Check(order != nullptr, "partial-fill order created");
  Check(broker.RiskApprove(order->id, &error) &&
            broker.Submit(order->id, &error) &&
            broker.Acknowledge(order->id, &error),
        "partial-fill order working");
  Quote first = GoodQuote(100.0);
  first.symbol = "AAA";
  first.ask = 100.0;
  first.ask_qty = 4;
  Check(broker.ApplyQuote(order->id, first, 1000, &error),
        "first partial fill processed");
  Check(order->filled_quantity == 2 && order->fills.size() == 1,
        "first fill lot is two shares");
  Check(broker.Reconcile(order->id, &error), "first partial fill reconciled");
  Check(portfolio->positions().at("AAA").quantity == 2,
        "two shares posted after first reconcile");

  Quote second = first;
  second.last = 110.0;
  second.ask = 110.0;
  second.ask_qty = 4;
  Check(broker.ApplyQuote(order->id, second, 1001, &error),
        "second partial fill processed");
  Check(order->filled_quantity == 4 && order->fills.size() == 2,
        "second fill lot recorded separately");
  Check(order->fills[1].notional_paise == 22000,
        "second fill notional is exact, not average-derived");
  Check(broker.Reconcile(order->id, &error), "second partial fill reconciled");
  const auto position = portfolio->positions().at("AAA");
  Check(position.quantity == 4, "all four shares posted");
  Check(position.avg_price == Money::FromPaise(10500),
        "combined average price is 105 rupees");
  int64_t invested = 0;
  Check(portfolio->ledger().invested.TryPaise(&invested) && invested == 42000,
        "combined notional is exactly 420 rupees");
  const Ledger before_retry = portfolio->ledger();
  Check(broker.Reconcile(order->id, &error), "repeated reconcile succeeds");
  Check(portfolio->ledger().cash == before_retry.cash &&
            portfolio->ledger().invested == before_retry.invested,
        "repeated reconcile does not mutate the portfolio");

  // The accounting events produced above must be consumable by the canonical
  // reducer after a restart.
  const auto events = journal.Replay(&error);
  Check(error.empty(), "paper journal replays cleanly");
  Portfolio rebuilt(Money::FromRupees(100000.0));
  std::set<std::string> applied;
  int postings = 0;
  for (const Event& event : events) {
    if (event.type != EventType::kAccounting)
      continue;
    AccountingPosting posting;
    Check(ParseAccountingPosting(event, &posting, &error),
          "paper accounting event matches canonical schema");
    if (!applied.insert(posting.posting_id).second)
      continue;
    Check(ApplyAccountingPosting(posting, &rebuilt, &error),
          "canonical reducer applies paper posting");
    ++postings;
  }
  Check(postings == 2, "two fill postings replayed");
  Check(rebuilt.ledger().cash == portfolio->ledger().cash &&
            rebuilt.ledger().invested == portfolio->ledger().invested &&
            rebuilt.ledger().realized_pnl == portfolio->ledger().realized_pnl &&
            rebuilt.positions().size() == portfolio->positions().size() &&
            rebuilt.positions().at("AAA").quantity ==
                portfolio->positions().at("AAA").quantity &&
            rebuilt.positions().at("AAA").avg_price ==
                portfolio->positions().at("AAA").avg_price,
        "rebuilt portfolio matches pre-restart state exactly");
  journal.Close();
}

void TestPaperExecution() {
  const std::string path = TempPath("paper_orders.jnl");
  std::remove(path.c_str());
  std::string error;
  Journal journal;
  Check(journal.Open(path, &error), "paper journal opens");
  auto portfolio = std::make_shared<Portfolio>(Money::FromRupees(100000.0));
  Config config = TestConfig();
  config.max_participation_fraction = 0.5;
  PaperBroker broker(config, &journal, portfolio);

  PaperOrder* intent = broker.CreateIntent(
      "AAA", OrderSide::kBuy, 4, Money::FromRupees(100.0), &error);
  Check(intent != nullptr, "paper intent created");
  Check(intent->state == PaperOrderState::kIntent, "intent starts at INTENT");
  Check(broker.Submit(intent->id, &error) == false,
        "unapproved intent cannot be submitted");
  Check(broker.RiskApprove(intent->id, &error), "risk approves intent");
  Check(broker.Submit(intent->id, &error), "approved order submitted");
  Check(broker.Acknowledge(intent->id, &error), "submission acknowledged");

  Quote above_limit = GoodQuote(100.0);
  above_limit.symbol = "AAA";
  above_limit.ask = 105.0;
  Check(broker.ApplyQuote(intent->id, above_limit, 1000, &error),
        "quote above limit is accepted without fill");
  Check(intent->filled_quantity == 0, "no fill above limit");
  Quote no_depth = above_limit;
  no_depth.ask = 100.0;
  no_depth.ask_qty = 0;
  Check(broker.ApplyQuote(intent->id, no_depth, 1001, &error),
        "touch without depth accepted");
  Check(intent->filled_quantity == 0, "limit touch without depth is no fill");

  Quote executable = no_depth;
  executable.ask_qty = 6;
  Check(broker.ApplyQuote(intent->id, executable, 1002, &error),
        "executable quote processed");
  Check(
      intent->filled_quantity > 0 && intent->filled_quantity < intent->quantity,
      "depth-capped partial fill");
  Check(intent->state == PaperOrderState::kPartiallyFilled,
        "partial fill state recorded");
  Check(portfolio->open_position_count() == 0,
        "fills are not posted before reconciliation");
  Check(broker.ApplyQuote(intent->id, executable, 1003, &error),
        "second quote processed");
  Check(intent->state == PaperOrderState::kFilled, "order fully filled");
  Check(broker.Reconcile(intent->id, &error), "fill reconciled to portfolio");
  Check(portfolio->open_position_count() == 1,
        "reconciliation posts one position");
  const Money cash_after_reconcile = portfolio->available_cash();
  Check(broker.Reconcile(intent->id, &error),
        "repeated reconciliation succeeds without mutation");
  Check(portfolio->available_cash() == cash_after_reconcile &&
            portfolio->open_position_count() == 1,
        "repeated reconciliation cannot duplicate a fill");
  Check(!broker.RiskApprove(intent->id, &error),
        "filled order cannot return to an earlier state");
  Check(!broker.Cancel(intent->id, &error), "filled order cannot be cancelled");

  PaperOrder* rejected = broker.CreateIntent(
      "BBB", OrderSide::kBuy, 1, Money::FromRupees(10.0), &error);
  Check(rejected != nullptr, "second intent created");
  Check(broker.Reject(rejected->id, "test rejection", &error),
        "order rejected");
  Check(rejected->state == PaperOrderState::kRejected,
        "rejected state recorded");
  Check(broker.Submit(rejected->id, &error) == false,
        "rejected order is terminal");

  PaperOrder* cancelled = broker.CreateIntent(
      "BBB", OrderSide::kBuy, 1, Money::FromRupees(10.0), &error);
  Check(broker.RiskApprove(cancelled->id, &error) &&
            broker.Submit(cancelled->id, &error) &&
            broker.Acknowledge(cancelled->id, &error),
        "cancel fixture working");
  Check(broker.Cancel(cancelled->id, &error), "working order cancelled");
  Check(cancelled->state == PaperOrderState::kCancelled,
        "cancelled state recorded");

  PaperOrder* ambiguous = broker.CreateIntent(
      "BBB", OrderSide::kBuy, 1, Money::FromRupees(10.0), &error);
  Check(broker.RiskApprove(ambiguous->id, &error) &&
            broker.Submit(ambiguous->id, &error) &&
            broker.Acknowledge(ambiguous->id, &error),
        "ambiguous fixture working");
  Check(broker.MarkAmbiguous(ambiguous->id, &error),
        "ambiguous outcome recorded");
  Check(!broker.Reconcile(ambiguous->id, &error),
        "ambiguous order cannot be auto-reconciled");
  PaperOrder* expiring = broker.CreateIntent(
      "BBB", OrderSide::kBuy, 1, Money::FromRupees(10.0), &error);
  Check(broker.RiskApprove(expiring->id, &error) &&
            broker.Submit(expiring->id, &error) &&
            broker.Acknowledge(expiring->id, &error),
        "expiry fixture working");
  Check(broker.Expire(expiring->id, &error), "expiry transition recorded");
  Check(broker.order_count() == 5, "all paper orders retained");
  Check(std::string(PaperOrderStateName(PaperOrderState::kAmbiguous)) ==
            "AMBIGUOUS",
        "state names are stable");
  journal.Close();
}

void TestExactIntegerPersistence() {
  const int64_t values[] = {
      (1LL << 53) - 1,
      (1LL << 53),
      (1LL << 53) + 1,
      std::numeric_limits<int64_t>::min(),
      std::numeric_limits<int64_t>::max(),
  };
  for (int64_t value : values) {
    Json encoded = Json::Integer(value);
    Json parsed;
    std::string error;
    Check(Json::Parse(encoded.Dump(), &parsed, &error),
          "exact integer JSON parses");
    int64_t decoded = 0;
    Check(parsed.as_integer(&decoded) && decoded == value,
          "exact integer survives JSON round trip");
    Check(parsed.is_exact_integer(), "integer type is explicit, not a double");
  }
  const uint64_t unsigned_values[] = {
      static_cast<uint64_t>(1ULL << 53) - 1,
      1ULL << 53,
      (1ULL << 53) + 1,
      std::numeric_limits<uint64_t>::max(),
  };
  for (uint64_t value : unsigned_values) {
    Json parsed;
    std::string error;
    Check(Json::Parse(Json::UInteger(value).Dump(), &parsed, &error),
          "unsigned integer JSON parses");
    uint64_t decoded = 0;
    Check(parsed.as_uinteger(&decoded) && decoded == value,
          "unsigned integer survives JSON round trip");
  }
  const std::string path = TempPath("risk_large.snap");
  std::remove(path.c_str());
  RiskSnapshot snapshot;
  snapshot.account_id = "PRIMARY";
  snapshot.session_id = "2026-11-26";
  snapshot.policy_version = "RISK-2026-11-26-v1";
  snapshot.journal_sequence = (1ULL << 53) + 1;
  snapshot.starting_equity_paise = std::numeric_limits<int64_t>::max() / 2;
  snapshot.current_equity_paise = snapshot.starting_equity_paise;
  snapshot.peak_equity_paise = snapshot.starting_equity_paise;
  snapshot.low_equity_paise = snapshot.starting_equity_paise;
  snapshot.halted = true;
  snapshot.halt_reason = "boundary fixture";
  snapshot.journal_identity = JournalIdentity({});
  std::string error;
  Check(SaveRiskSnapshot(path, snapshot, &error),
        "large-integer risk snapshot saves");
  RiskSnapshot loaded;
  Check(LoadRiskSnapshot(path, &loaded, &error),
        "large-integer risk snapshot loads");
  Check(loaded.journal_sequence == snapshot.journal_sequence &&
            loaded.starting_equity_paise == snapshot.starting_equity_paise,
        "large integers round trip without precision loss");
  std::remove(path.c_str());
}

void TestKdbxCheckpointContract() {
  const std::string path = TempPath("kdbx_checkpoint.json");
  std::remove(path.c_str());
  std::string error;
  KdbxCheckpoint checkpoint;
  checkpoint.journal_watermark = 7;
  checkpoint.quote_count = 12;
  checkpoint.bar_count = 3;
  checkpoint.feature_count = 2;
  checkpoint.complete = true;
  checkpoint.runtime_version = "q-4.2.0";
  checkpoint.created_at_ms = 1000;
  Check(SaveKdbxCheckpoint(path, checkpoint, &error),
        "complete checkpoint saved");
  KdbxCheckpoint loaded;
  Check(LoadKdbxCheckpoint(path, &loaded, &error),
        "checkpoint loads with valid checksum");
  Check(loaded.journal_watermark == 7 && loaded.quote_count == 12,
        "checkpoint fields round trip");
  std::vector<Event> journal;
  for (uint64_t sequence = 1; sequence <= 10; ++sequence) {
    Event event;
    event.sequence = sequence;
    event.type = sequence % 2 == 0 ? EventType::kQuote : EventType::kCandle;
    journal.push_back(event);
  }
  std::vector<Event> replay;
  Check(KdbxRecoveryPlan(loaded, journal, &replay, &error),
        "recovery plan built from checkpoint watermark");
  Check(replay.size() == 3 && replay.front().sequence == 8,
        "only post-watermark events are replayed");
  KdbxCheckpoint ahead = loaded;
  ahead.journal_watermark = 11;
  Check(!KdbxRecoveryPlan(ahead, journal, &replay, &error),
        "watermark beyond journal is rejected");
  KdbxCheckpoint incomplete = loaded;
  incomplete.complete = false;
  Check(!SaveKdbxCheckpoint(path, incomplete, &error),
        "incomplete checkpoint is never committed");
  std::ofstream corrupt(path, std::ios::trunc);
  corrupt << "garbage";
  corrupt.close();
  Check(!LoadKdbxCheckpoint(path, &loaded, &error),
        "corrupt checkpoint is rejected");
  std::remove(path.c_str());
}

void TestRiskPersistence() {
  const std::string journal_path = TempPath("risk_state.jnl");
  const std::string snapshot_path = TempPath("risk_state.snap");
  std::remove(journal_path.c_str());
  std::remove(snapshot_path.c_str());
  std::string error;
  Journal journal;
  Check(journal.Open(journal_path, &error), "risk journal opens");
  DailyRiskState original;
  original.date = "2026-11-26";
  original.starting_equity = Money::FromRupees(500.0);
  original.current_equity = original.starting_equity;
  original.peak_equity = original.starting_equity;
  original.low_equity = original.starting_equity;
  original.realized_pnl = Money::FromPaise(0);
  original.unrealized_pnl = Money::FromPaise(0);
  Check(AppendRiskEvent(
            &journal, "RISK_SESSION_INITIALIZED", &original, 1000, &error),
        "session initialised durably");
  original.current_equity = Money::FromRupees(480.0);
  original.low_equity = original.current_equity;
  Check(AppendRiskEvent(&journal, "EQUITY_MARKED", &original, 2000, &error),
        "equity mark durably recorded");
  original.halted = true;
  original.halt_reason = "daily loss limit reached";
  Check(AppendRiskEvent(
            &journal, "KILL_SWITCH_ACTIVATED", &original, 3000, &error),
        "kill switch durably recorded");
  journal.Close();

  DailyRiskState recovered;
  Check(RecoverRiskState(snapshot_path, journal_path, &recovered, &error),
        "risk state rebuilt from journal without snapshot");
  Check(recovered.halted, "breached kill switch survives recovery");
  Check(recovered.halt_reason == "daily loss limit reached",
        "halt reason survives recovery");
  Check(recovered.low_equity == Money::FromRupees(480.0),
        "session low survives recovery");
  Check(recovered.journal_sequence == 3, "journal watermark restored");

  Check(SaveRiskSnapshot(
            snapshot_path, SnapshotFromState(recovered, journal_path), &error),
        "checkpoint saved atomically");
  Journal later;
  Check(later.Open(journal_path, &error), "journal reopens for new events");
  DailyRiskState after = recovered;
  after.current_equity = Money::FromRupees(470.0);
  after.low_equity = after.current_equity;
  Check(AppendRiskEvent(&later, "EQUITY_MARKED", &after, 4000, &error),
        "post-checkpoint event recorded");
  later.Close();
  DailyRiskState replayed;
  Check(RecoverRiskState(snapshot_path, journal_path, &replayed, &error),
        "recovery replays events after the checkpoint");
  Check(replayed.low_equity == Money::FromRupees(470.0),
        "post-checkpoint equity applied");
  Check(replayed.halted && replayed.journal_sequence == 4,
        "kill switch and sequence remain intact after replay");

  std::ofstream corrupt(snapshot_path, std::ios::trunc);
  corrupt << "not a snapshot";
  corrupt.close();
  DailyRiskState rebuilt;
  Check(RecoverRiskState(snapshot_path, journal_path, &rebuilt, &error),
        "corrupt snapshot is rebuilt from the journal");
  Check(rebuilt.halted && rebuilt.low_equity == Money::FromRupees(470.0),
        "rebuild reproduces authoritative risk state");

  RiskSnapshot inconsistent = SnapshotFromState(rebuilt, journal_path);
  inconsistent.low_equity_paise = 999999;  // above current equity.
  Check(SaveRiskSnapshot(snapshot_path, inconsistent, &error),
        "inconsistent checkpoint written for rejection test");
  DailyRiskState rejected;
  Check(!RecoverRiskState(snapshot_path, journal_path, &rejected, &error),
        "inconsistent reconstructed state is rejected");
  Check(error.find("inconsistent") != std::string::npos,
        "inconsistency is explicit");

  RiskSnapshot foreign = SnapshotFromState(rebuilt, journal_path);
  Check(SaveRiskSnapshot(snapshot_path, foreign, &error),
        "foreign-account checkpoint saved");
  Check(!RecoverRiskState(
            snapshot_path, journal_path, &rejected, "OTHER", &error),
        "checkpoint from another account is rejected");
  Check(error.find("account") != std::string::npos,
        "account mismatch is explicit");

  RiskSnapshot ahead = SnapshotFromState(rebuilt, journal_path);
  ahead.journal_sequence = 9999;
  Check(SaveRiskSnapshot(snapshot_path, ahead, &error),
        "ahead-watermark checkpoint saved");
  Check(!RecoverRiskState(snapshot_path, journal_path, &rejected, &error),
        "watermark beyond journal is rejected");
  Check(error.find("ahead") != std::string::npos,
        "watermark rejection is explicit");

  RiskSnapshot forged = SnapshotFromState(rebuilt, journal_path);
  forged.journal_identity = "not-the-journal";
  Check(SaveRiskSnapshot(snapshot_path, forged, &error),
        "forged identity checkpoint saved");
  Check(!RecoverRiskState(snapshot_path, journal_path, &rejected, &error),
        "checkpoint that does not belong to the journal is rejected");
  std::remove(snapshot_path.c_str());

  DailyRiskState fresh;
  const Config config = TestConfig();
  Journal decision_journal;
  Check(decision_journal.Open(journal_path, &error),
        "journal opens for engine persistence test");
  auto shared = std::make_shared<DailyRiskState>(fresh);
  RiskEngine engine(config, shared, &decision_journal);
  Signal signal;
  signal.symbol = "AAA";
  signal.tradable = false;
  signal.rejection_reason = "test";
  Quote quote;
  quote.last = 100.0;
  quote.bid = 99.9;
  quote.ask = 100.1;
  quote.bid_qty = 1000;
  quote.ask_qty = 1000;
  quote.exchange_timestamp_ms = 100000;
  quote.provider_timestamp_ms = 100000;
  quote.receive_timestamp_ms = 100000;
  quote.quality = "ok";
  Portfolio portfolio(Money::FromRupees(500.0));
  (void)engine.Evaluate(signal, quote, portfolio, 100000);
  Check(shared->journal_sequence == 5,
        "risk engine persists a session event before decisions");
  decision_journal.Close();
}

void TestFeatureCrossLanguage() {
  // Independent reference values produced by python/research.py on this exact
  // unequal-volume fixture. Tolerance-based equivalence is required.
  std::vector<Candle> candles;
  for (int i = 0; i < 25; ++i) {
    const double close = 100.0 + 0.7 * i + 2.0 * std::sin(i);
    Candle candle;
    candle.date = "D" + std::to_string(i);
    candle.open = close;
    candle.high = close + 3.0;
    candle.low = close - 2.0;
    candle.close = close;
    candle.volume = 1000.0 + 37.0 * i;
    candles.push_back(candle);
  }
  const FeatureSet features = ComputeFeatures(candles);
  Check(features.valid, "cross-language fixture warms up");
  Near(features.ret5, 0.012227921299313493, 1e-12, "ret5 matches Python");
  Near(features.ret20, 0.13528419355169152, 1e-12, "ret20 matches Python");
  Near(features.vol20,
       0.012173429881693497,
       1e-12,
       "population vol20 matches Python");
  Near(features.volume_surprise,
       0.22876667751383017,
       1e-12,
       "volume surprise matches Python");
  Near(features.vwap_dev,
       0.03664852280982922,
       1e-12,
       "volume-weighted typical-price proxy matches Python");
  Near(features.trend, 1.0, 1e-12, "trend matches Python");
  Near(features.breakout, 0.5569266238518116, 1e-12, "breakout matches Python");
  Check(!ComputeFeatures(
             std::vector<Candle>(candles.begin(), candles.begin() + 20))
             .valid,
        "short warm-up is invalid rather than a valid zero");
}

// Independent reference ledger for property-based accounting checks.
struct ReferenceLedger {
  int64_t cash = 0;
  int64_t invested = 0;
  int64_t realized = 0;
  int64_t operating = 0;
  int64_t peak = 0;
  struct RefPosition {
    int quantity = 0;
    int64_t average = 0;
    int64_t last = 0;
  };
  std::map<std::string, RefPosition> positions;

  int64_t unrealized() const {
    int64_t total = 0;
    for (const auto& [symbol, position] : positions) {
      (void)symbol;
      total += static_cast<int64_t>(position.quantity) *
               (position.last - position.average);
    }
    return total;
  }
  int64_t equity() const {
    return cash + invested + unrealized();
  }
  void mark_peak() {
    peak = std::max(peak, equity());
  }
  int64_t drawdown() const {
    return std::max<int64_t>(0, peak - equity());
  }
};

void TestMoneyAdversarial() {
  const Money invalid = Money::Invalid();
  const Money zero = Money::FromPaise(0);
  Check(!(invalid <= zero), "invalid is not less-or-equal to zero");
  Check(!(invalid >= zero), "invalid is not greater-or-equal to zero");
  Check(!(invalid < zero) && !(invalid > zero),
        "invalid is unordered against zero");
  Check(!(invalid == zero) && !(invalid == invalid),
        "invalid never compares equal");
  int64_t extracted = -1;
  double rupees = -1.0;
  Check(!invalid.TryPaise(&extracted) && extracted == -1,
        "invalid paise extraction fails explicitly");
  Check(!invalid.TryRupees(&rupees) && rupees == -1.0,
        "invalid rupee extraction fails explicitly");
  Check(zero.TryPaise(&extracted) && extracted == 0,
        "valid zero extracts normally");
  Check(!(invalid + Money::FromPaise(100)).valid(),
        "invalid plus valid propagates invalid");
  Check(!(Money::FromPaise(100) - invalid).valid(),
        "valid minus invalid propagates invalid");
  const int64_t minimum = std::numeric_limits<int64_t>::min();
  const int64_t maximum = std::numeric_limits<int64_t>::max();
  Check(!(zero - Money::FromPaise(minimum)).valid(),
        "zero minus INT64_MIN is rejected without UB");
  Check((Money::FromPaise(minimum) - Money::FromPaise(minimum)).valid(),
        "INT64_MIN minus itself is zero");
  Check(!(Money::FromPaise(maximum) - Money::FromPaise(-1)).valid(),
        "INT64_MAX minus negative one overflows");
  Check((Money::FromPaise(-5) - Money::FromPaise(-3)) == Money::FromPaise(-2),
        "negative subtraction is exact");
  Check((Money::FromPaise(maximum) + Money::FromPaise(1)).valid() == false,
        "addition overflow is invalid");
  int64_t product = 0;
  Check(!money_detail::CheckedMultiply(minimum, -1, &product),
        "INT64_MIN times minus one is rejected");
  Check(money_detail::CheckedMultiply(-3, 7, &product) && product == -21,
        "signed multiplication is exact");
}

void TestPortfolioProperty() {
  uint64_t state = 0x9e3779b97f4a7c15ULL;
  auto next = [&state]() {
    state ^= state << 13;
    state ^= state >> 7;
    state ^= state << 17;
    return state;
  };
  CostBreakdown zero_costs;
  zero_costs.status = CalculationStatus::kCalculated;
  const std::vector<std::string> symbols = {"AAA", "BBB", "CCC"};
  for (int run = 0; run < 40; ++run) {
    Portfolio portfolio(Money::FromRupees(100000.0));
    ReferenceLedger reference;
    reference.cash = 10000000;
    reference.peak = 10000000;
    std::string error;
    for (int step = 0; step < 60; ++step) {
      const std::string& symbol = symbols[next() % symbols.size()];
      const int operation = static_cast<int>(next() % 10);
      const int quantity = 1 + static_cast<int>(next() % 5);
      const int64_t price = (100 + static_cast<int>(next() % 200)) * 100;
      if (operation < 4) {
        const bool ok = portfolio.Buy(
            symbol, quantity, Money::FromPaise(price), zero_costs, &error);
        auto it = reference.positions.find(symbol);
        bool expected = it == reference.positions.end() ||
                        price * quantity <= reference.cash;
        Check(ok == expected, "property buy acceptance");
        if (ok) {
          if (it == reference.positions.end()) {
            ReferenceLedger::RefPosition position;
            position.quantity = quantity;
            position.average = price;
            position.last = price;
            reference.positions[symbol] = position;
          } else {
            const int total = it->second.quantity + quantity;
            it->second.average =
                (it->second.average * it->second.quantity + price * quantity) /
                total;
            it->second.quantity = total;
            it->second.last = price;
          }
          reference.cash -= price * quantity;
          reference.invested += price * quantity;
        }
      } else if (operation < 7) {
        const bool ok = portfolio.Sell(
            symbol, quantity, Money::FromPaise(price), zero_costs, &error);
        auto it = reference.positions.find(symbol);
        bool expected =
            it != reference.positions.end() && it->second.quantity >= quantity;
        Check(ok == expected, "property sell acceptance");
        if (ok) {
          reference.cash += price * quantity;
          reference.invested -= it->second.average * quantity;
          reference.realized += (price - it->second.average) * quantity;
          it->second.quantity -= quantity;
          if (it->second.quantity == 0)
            reference.positions.erase(it);
          else
            it->second.last = price;
        }
      } else if (operation < 9) {
        const bool ok =
            portfolio.MarkToMarket(symbol, Money::FromPaise(price), &error);
        auto it = reference.positions.find(symbol);
        Check(ok == (it != reference.positions.end()),
              "property mark acceptance");
        if (ok)
          it->second.last = price;
      } else {
        const int64_t amount = (1 + static_cast<int>(next() % 50)) * 100;
        const bool ok =
            portfolio.RecordOperatingExpense(Money::FromPaise(amount), &error);
        Check(ok == (amount <= reference.cash), "property expense acceptance");
        if (ok) {
          reference.cash -= amount;
          reference.operating += amount;
        }
      }
      reference.mark_peak();
      const auto ledger = portfolio.ledger();
      int64_t actual_cash = 0;
      int64_t actual_invested = 0;
      int64_t actual_realized = 0;
      int64_t actual_operating = 0;
      int64_t actual_peak = 0;
      Check(ledger.cash.TryPaise(&actual_cash) &&
                ledger.invested.TryPaise(&actual_invested) &&
                ledger.realized_pnl.TryPaise(&actual_realized) &&
                ledger.operating.TryPaise(&actual_operating) &&
                ledger.peak_equity.TryPaise(&actual_peak),
            "property ledger values are valid");
      if (actual_cash != reference.cash ||
          actual_invested != reference.invested ||
          actual_realized != reference.realized ||
          actual_operating != reference.operating ||
          actual_peak != reference.peak) {
        Check(false, "property ledger reconciliation");
        break;
      }
      int64_t actual_drawdown = 0;
      Check(portfolio.drawdown().TryPaise(&actual_drawdown) &&
                actual_drawdown == reference.drawdown(),
            "property drawdown reconciliation");
      for (const auto& [symbol_name, position] : portfolio.positions()) {
        auto it = reference.positions.find(symbol_name);
        int64_t price_paise = 0;
        Check(it != reference.positions.end() &&
                  position.last_price.TryPaise(&price_paise) &&
                  price_paise == it->second.last,
              "property position mark reconciliation");
      }
    }
  }
}

void TestTerminalAccounting() {
  std::map<std::string, std::vector<Candle>> history;
  history["NIFTY 50"] = SyntheticCandles(200, 20000.0, 0.001);
  history["AAA"] = SyntheticCandles(200, 100.0, 0.003);
  Config config = TestConfig();
  config.terminal_liquidation = true;
  const BacktestMetrics liquidated = RunBacktest(history, config);
  Check(liquidated.terminal_liquidation_requested,
        "terminal liquidation requested is reported");
  Check(!liquidated.terminal_liquidation_failed,
        "valid prices do not fail terminal liquidation");
  Check(liquidated.terminal_open_positions == liquidated.open_positions,
        "terminal residual matches final open positions");
  if (liquidated.terminal_open_positions == 0) {
    Check(liquidated.terminal_liquidation_completed,
          "zero residual positions complete liquidation");
  }
  Config no_liquidation = TestConfig();
  no_liquidation.terminal_liquidation = false;
  const BacktestMetrics marked = RunBacktest(history, no_liquidation);
  Check(!marked.terminal_liquidation_requested,
        "terminal liquidation not requested when disabled");
  Check(!marked.terminal_liquidation_completed,
        "disabled liquidation is never reported as completed");
}

int main() {
  using namespace quant;
  TestMoney();
  TestAccountingReplayIntegrity();
  TestCanonicalDateFixture();
  TestMoneyAdversarial();
  TestConfigParsing();
  TestCosts();
  TestAccounting();
  TestPortfolioProperty();
  TestRisk();
  TestFeatures();
  TestFeatureCrossLanguage();
  TestJournal();
  TestJson();
  TestNormalization();
  TestOrders();
  TestBacktest();
  TestTerminalAccounting();
  TestGunzip();
  TestCsv();
  TestFreshnessSemantics();
  TestDailyRiskLimit();
  TestRiskPersistence();
  TestMarkedSaleAndCostBoundaries();
  TestHighWaterAndMoneyBounds();
  TestDynamicSizing();
  TestSizingConstraints();
  TestExecutionSemantics();
  TestKdbxGate();
  TestKdbxCheckpointContract();
  TestExactIntegerPersistence();
  TestPaperExecution();
  TestPaperPartialAndReplay();
  TestMarketDataPipeline();
  std::cout << "checks=" << g_checks << " failures=" << g_failures << "\n";
  return g_failures == 0 ? 0 : 1;
}
