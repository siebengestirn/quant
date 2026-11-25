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
  const CostBreakdown first =
      agg.Estimate(Money::FromRupees(1000.0), true, false, 0.0, "X", "D1");
  const CostBreakdown second =
      agg.Estimate(Money::FromRupees(1000.0), true, false, 0.0, "X", "D1");
  Near(first.dp.rupees(), 15.34, 0.01, "first delivery sell incurs DP");
  Check(second.dp.paise() == 0, "second same-day sell has no DP charge");
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
  const CostBreakdown zero;
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

int main() {
  using namespace quant;
  TestMoney();
  TestConfigParsing();
  TestCosts();
  TestAccounting();
  TestRisk();
  TestFeatures();
  TestJournal();
  TestJson();
  TestNormalization();
  TestOrders();
  TestBacktest();
  TestGunzip();
  TestCsv();
  TestFreshnessSemantics();
  TestDailyRiskLimit();
  TestMarkedSaleAndCostBoundaries();
  TestHighWaterAndMoneyBounds();
  TestDynamicSizing();
  TestSizingConstraints();
  TestExecutionSemantics();
  TestKdbxGate();
  std::cout << "checks=" << g_checks << " failures=" << g_failures << "\n";
  return g_failures == 0 ? 0 : 1;
}
