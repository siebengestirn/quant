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

int64_t NowMs() {
  return std::chrono::duration_cast<std::chrono::milliseconds>(
             std::chrono::system_clock::now().time_since_epoch())
      .count();
}

Quote GoodQuote(double price = 100.0) {
  Quote quote;
  quote.symbol = "TEST";
  quote.permanent_id = "INSTRUMENT-TEST";
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
  signal.permanent_id = "INSTRUMENT-TEST";
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

Quote MarketQuoteFor(const PaperOrder& order) {
  Quote quote = GoodQuote(order.limit_price.rupees());
  quote.symbol = order.symbol;
  quote.permanent_id = order.instrument_key;
  quote.venue = order.venue;
  quote.listing_id = order.listing_id;
  quote.ask = order.limit_price.rupees();
  quote.bid = quote.ask * 0.999;
  quote.last = quote.ask;
  quote.ask_qty = 1000000;
  quote.bid_qty = 1000000;
  quote.sequence = 7;
  return quote;
}

RiskAuthorization ApprovedAuthorization(const PaperOrder& order) {
  Config config = TestConfig();
  config.max_participation_fraction = 1.0;
  config.max_open_positions = 100;
  auto state = std::make_shared<DailyRiskState>();
  RiskEngine authority(config, state);
  Portfolio deep(Money::FromRupees(100000000.0));
  Signal signal;
  signal.symbol = order.symbol;
  signal.permanent_id = order.instrument_key;
  signal.venue = order.venue;
  signal.listing_id = order.listing_id;
  signal.tradable = true;
  return authority.Authorize(
      signal, MarketQuoteFor(order), deep, order, NowMs());
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

std::string StagingPath(const std::string& target, const std::string& id) {
  return (std::filesystem::path(target).parent_path() / (".staging." + id))
      .string();
}

void TestUniversePublishRecovery() {
  const std::string path = TempPath("universe_publish.csv");
  const std::string journal_path = TempPath("universe_publish.jnl");
  std::remove(path.c_str());
  for (const char* id : {"TX-1", "TX-2", "TX-3", "TX-4"})
    std::remove(StagingPath(path, id).c_str());
  std::remove(journal_path.c_str());
  std::string error;
  Journal journal;
  Check(journal.Open(journal_path, &error), "publication journal opens");
  std::ofstream initial(path);
  initial << "old\n";
  initial.close();

  Check(!PublishUniverse(path,
                         "new\n",
                         &journal,
                         "TX-1",
                         PublicationFailurePoint::kAfterAuditBeforeRename,
                         &error),
        "injected audit/rename failure reported");
  std::ifstream after_failure(path);
  std::string published((std::istreambuf_iterator<char>(after_failure)),
                        std::istreambuf_iterator<char>());
  after_failure.close();
  Check(published == "old\n", "failed publication leaves prior file intact");
  Check(std::filesystem::exists(StagingPath(path, "TX-1")),
        "audited staging artifact retained for recovery");

  Check(RecoverUniversePublication(path, &journal, nullptr, &error),
        "startup reconciliation completes pending publication");
  std::ifstream recovered(path);
  published.assign(std::istreambuf_iterator<char>(recovered),
                   std::istreambuf_iterator<char>());
  recovered.close();
  Check(published == "new\n", "recovered publication has audited content");
  Check(!std::filesystem::exists(StagingPath(path, "TX-1")),
        "staging artifact consumed by recovery");
  Check(RecoverUniversePublication(path, &journal, nullptr, &error),
        "second reconciliation is idempotent");

  Check(!PublishUniverse(path,
                         "newer\n",
                         &journal,
                         "TX-2",
                         PublicationFailurePoint::kAfterRenameBeforeCommit,
                         &error),
        "injected rename/commit failure reported");
  UniverseRecoveryReport recovery_report;
  Check(RecoverUniversePublication(path, &journal, &recovery_report, &error),
        "post-rename failure reconciles");
  Check(recovery_report.recovered == 1,
        "rename-before-commit transaction is committed on recovery");
  auto commit_count = [&]() {
    size_t count = 0;
    for (const Event& event : journal.Replay(&error)) {
      Json payload;
      if (event.type != EventType::kHeartbeat ||
          !Json::Parse(event.payload, &payload, &error))
        continue;
      const Json* action = payload.Find("action");
      if (action != nullptr && action->as_string() == "UNIVERSE_PUBLISH_COMMIT")
        ++count;
    }
    return count;
  };
  Check(commit_count() == 2,
        "recovery appends exactly one missing commit record");
  std::ifstream final_input(path);
  published.assign(std::istreambuf_iterator<char>(final_input),
                   std::istreambuf_iterator<char>());
  final_input.close();
  Check(published == "newer\n", "committed content is authoritative");

  Check(PublishUniverse(path,
                        "normal\n",
                        &journal,
                        "TX-3",
                        PublicationFailurePoint::kNone,
                        &error),
        "normal publication succeeds");
  Check(!PublishUniverse(path,
                         "no-tx\n",
                         &journal,
                         "",
                         PublicationFailurePoint::kNone,
                         &error),
        "publication without transaction id rejected");

  Check(PublishUniverse(path,
                        "normal\n",
                        &journal,
                        "TX-3",
                        PublicationFailurePoint::kNone,
                        &error),
        "identical retry after commit is idempotent");
  Check(commit_count() == 3,
        "idempotent retry appends no additional commit record");
  Check(!PublishUniverse(path,
                         "different\n",
                         &journal,
                         "TX-3",
                         PublicationFailurePoint::kNone,
                         &error),
        "reused transaction id with different payload rejected");
  std::ifstream after_conflict(path);
  published.assign(std::istreambuf_iterator<char>(after_conflict),
                   std::istreambuf_iterator<char>());
  after_conflict.close();
  Check(published == "normal\n",
        "conflicting retry does not touch committed content");
  Check(!error.empty(), "conflicting retry reports an explicit reason");

  std::ofstream tampered(StagingPath(path, "TX-4"));
  tampered << "tampered\n";
  tampered.close();
  Check(!PublishUniverse(path,
                         "tx4\n",
                         &journal,
                         "TX-4",
                         PublicationFailurePoint::kAfterAuditBeforeRename,
                         &error),
        "pre-existing staging path blocks a new transaction");
  UniverseRecoveryReport mismatch_report;
  std::ifstream after_tamper(path);
  published.assign(std::istreambuf_iterator<char>(after_tamper),
                   std::istreambuf_iterator<char>());
  after_tamper.close();
  Check(published == "normal\n", "tampered staging never published");
  journal.Close();
}

EventEnvelope MakeEnvelope(const std::string& id,
                           const std::string& partition,
                           uint64_t sequence,
                           uint64_t offset) {
  EventEnvelope event;
  event.event_id = id;
  event.partition_key = partition;
  event.source_sequence = sequence;
  event.replay_offset = offset;
  event.event_time_ms = 1000 + static_cast<int64_t>(sequence);
  event.receive_time_ms = 2000 + static_cast<int64_t>(sequence);
  event.payload = "quote|" + id;
  return event;
}

void TestDeterministicEventRuntime() {
  EventRuntime runtime(2, QueuePolicy::kReject);
  std::string error;
  Check(runtime.Submit(MakeEnvelope("E1", "AAA", 1, 10), &error),
        "first event accepted");
  Check(runtime.Submit(MakeEnvelope("E2", "AAA", 2, 11), &error),
        "second event accepted");
  Check(!runtime.Submit(MakeEnvelope("E3", "AAA", 3, 12), &error),
        "bounded queue rejects overflow");
  Check(runtime.queue_depth() == 2, "queue depth bounded at capacity");
  Check(runtime.Submit(MakeEnvelope("E1", "AAA", 1, 10), &error),
        "uncommitted duplicate is idempotent by queued identity");
  Check(runtime.result().committed == 0,
        "submit alone does not commit an event");
  Check(runtime.ProcessOne(&error) && runtime.ProcessOne(&error),
        "queued events committed");
  Check(runtime.Submit(MakeEnvelope("E1", "AAA", 1, 10), &error),
        "identical retry accepted after commit");
  Check(runtime.result().duplicates == 2,
        "duplicate counted for queued and committed retries");
  Check(runtime.queue_depth() == 0, "duplicate did not enqueue");
  EventEnvelope conflicting = MakeEnvelope("E1", "AAA", 1, 10);
  conflicting.payload = "different";
  Check(!runtime.Submit(conflicting, &error), "conflicting id rejected");
  Check(runtime.result().conflicts == 1 && runtime.result().quarantined,
        "conflict quarantines runtime");
  Check(!runtime.Submit(MakeEnvelope("E4", "AAA", 1, 13), &error),
        "non-monotonic source sequence rejected");

  RuntimeResult drained = runtime.Drain(&error);
  Check(drained.committed == 2, "all queued events committed once");
  Check(runtime.queue_depth() == 0, "queue drained");
  Check(runtime.offsets().at("AAA") == 11,
        "partition replay offset is monotonic");
  Check(runtime.sequences().at("AAA") == 2,
        "partition source sequence watermark advanced");
  Check(runtime.ProcessOne(&error) == false, "empty queue has no event");

  EventRuntime ordered(4, QueuePolicy::kReject);
  Check(ordered.Submit(MakeEnvelope("O1", "CCC", 1, 50), &error),
        "ordered fixture accepted");
  Check(ordered.ProcessOne(&error), "ordered fixture committed");
  Check(ordered.Submit(MakeEnvelope("O2", "CCC", 2, 40), &error),
        "backward-offset event accepted into queue");
  const size_t depth_before = ordered.queue_depth();
  Check(!ordered.ProcessOne(&error), "backward offset rejected at commit");
  Check(ordered.queue_depth() == depth_before,
        "backward-offset event retained, not silently lost");
  Check(ordered.Submit(MakeEnvelope("O2", "CCC", 2, 40), &error),
        "retry of queued event is idempotent, not a second enqueue");
  Check(ordered.queue_depth() == depth_before,
        "retry does not add a second queued event");

  EventRuntime replay(8, QueuePolicy::kRetry);
  for (uint64_t i = 1; i <= 5; ++i) {
    Check(replay.Submit(
              MakeEnvelope("R" + std::to_string(i), "BBB", i, i + 100), &error),
          "replay fixture accepted");
  }
  replay.Drain(&error);
  Check(replay.offsets().at("BBB") == 105,
        "replay reconstructs exact partition offset");
  Check(replay.result().committed == 5 && replay.applied() == 5,
        "no replayed event applied twice");
}

void TestPaperRehydration() {
  const std::string path = TempPath("paper_rehydrate.jnl");
  std::remove(path.c_str());
  std::string error;
  Journal journal;
  Check(journal.Open(path, &error), "rehydration journal opens");
  auto portfolio = std::make_shared<Portfolio>(Money::FromRupees(100000.0));
  Config config = TestConfig();
  config.max_participation_fraction = 0.5;
  PaperBroker first(config, &journal, portfolio);
  PaperOrder* order = first.CreateIntent(
      "AAA", OrderSide::kBuy, 4, Money::FromRupees(200.0), &error);
  Check(order != nullptr, "order created for rehydration");
  const std::string original_id = order->id;
  Check(first.RiskApprove(order->id,
                          ApprovedAuthorization(*order),
                          MarketQuoteFor(*order),
                          NowMs(),
                          &error) &&
            first.Submit(order->id, &error) &&
            first.Acknowledge(order->id, &error),
        "order advanced for rehydration");
  Quote quote = GoodQuote(100.0);
  quote.symbol = "AAA";
  quote.ask = 100.0;
  quote.ask_qty = 4;
  Check(first.ApplyQuote(order->id, quote, NowMs(), &error),
        "fill created for rehydration");
  Check(first.Reconcile(order->id, &error), "fill reconciled");
  const int64_t notional = order->cumulative_notional_paise;
  Check(notional == 20000, "cumulative notional preserved exactly");

  PaperBroker second(config, &journal, portfolio);
  Check(second.RehydrateFromJournal(&error), "orders rehydrated from journal");
  const PaperOrder* restored = second.Find(original_id);
  Check(restored != nullptr && restored->fills.size() == 1,
        "fill lot rehydrated once");
  Check(restored->cumulative_notional_paise == notional,
        "cumulative notional rehydrated exactly");
  Check(restored->posted_fill_count == 1, "posting state rehydrated");
  Check(second.Reconcile(original_id, &error),
        "reconcile after restart is idempotent");
  Check(portfolio->open_position_count() == 1,
        "restart does not duplicate portfolio posting");

  PaperOrder* fresh = second.CreateIntent(
      "BBB", OrderSide::kBuy, 1, Money::FromRupees(10.0), &error);
  Check(fresh != nullptr && fresh->id != original_id,
        "restarted broker does not reuse order ids");

  auto poor = std::make_shared<Portfolio>(Money::FromRupees(1.0));
  PaperBroker preflight(config, &journal, poor);
  Check(preflight.RehydrateFromJournal(&error), "preflight broker rehydrated");
  auto accounting_events = [&]() {
    size_t count = 0;
    for (const Event& event : journal.Replay(&error))
      if (event.type == EventType::kAccounting)
        ++count;
    return count;
  };
  const size_t before = accounting_events();
  PaperOrder* too_big = preflight.CreateIntent(
      "CCC", OrderSide::kBuy, 1000, Money::FromRupees(100.0), &error);
  Check(too_big != nullptr, "oversized intent accepted for preflight test");
  Check(preflight.RiskApprove(too_big->id,
                              ApprovedAuthorization(*too_big),
                              MarketQuoteFor(*too_big),
                              NowMs(),
                              &error) &&
            preflight.Submit(too_big->id, &error) &&
            preflight.Acknowledge(too_big->id, &error),
        "oversized order advanced");
  Quote affordable = GoodQuote(100.0);
  affordable.symbol = "CCC";
  affordable.ask = 100.0;
  affordable.ask_qty = 1000;
  Check(preflight.ApplyQuote(too_big->id, affordable, NowMs(), &error),
        "oversized fill simulated");
  Check(!preflight.Reconcile(too_big->id, &error),
        "posting rejected by portfolio preflight");
  Check(accounting_events() == before,
        "rejected posting appended no accounting event");
  journal.Close();
}

void TestAssetAndComplianceContracts() {
  InstrumentSpec equity;
  equity.exchange = "NSE";
  equity.calendar_id = "NSE-CALENDAR-v1";
  equity.fee_schedule_id = "ZERODHA-EQ-2026-11-25-v1";
  equity.tick_size = 0.05;
  equity.lot_size = 1;
  equity.venue_supported = true;
  std::string error;
  Check(ValidateInstrumentSpec(equity, &error), "equity spec valid");
  InstrumentSpec bad = equity;
  bad.tick_size = 0.0;
  Check(!ValidateInstrumentSpec(bad, &error), "zero tick rejected");
  bad = equity;
  bad.lot_size = -1;
  Check(!ValidateInstrumentSpec(bad, &error), "negative lot rejected");
  bad = equity;
  bad.venue_supported = false;
  Check(!ValidateInstrumentSpec(bad, &error), "unsupported venue rejected");
  Check(ExecutionProfileSupported(ExecutionProfile::kDaily),
        "daily profile available");
  Check(!ExecutionProfileSupported(ExecutionProfile::kIntraday) &&
            !ExecutionProfileSupported(ExecutionProfile::kHft),
        "intraday and HFT profiles are not available");

  ComplianceGateState gate;
  Check(!LiveRouteAuthorized(gate, &error), "empty gate blocks live route");
  gate.written_broker_approval = true;
  gate.compliance_signoff = true;
  gate.operational_risk_signoff = true;
  gate.sandbox_certified = true;
  gate.approval_ref = "SIGN-OFF-1";
  Check(!LiveRouteAuthorized(gate, &error),
        "complete gate still blocks live route in this build");
}

void TestFillAverageArithmetic() {
  const std::string path = TempPath("fill_average.jnl");
  std::remove(path.c_str());
  std::string error;
  Journal journal;
  Check(journal.Open(path, &error), "average journal opens");
  auto portfolio = std::make_shared<Portfolio>(Money::FromRupees(100000.0));
  Config config = TestConfig();
  config.max_participation_fraction = 1.0;
  PaperBroker broker(config, &journal, portfolio);
  PaperOrder* order = broker.CreateIntent(
      "AAA", OrderSide::kBuy, 4, Money::FromRupees(110.0), &error);
  Check(order != nullptr, "four-share order created");
  broker.RiskApprove(order->id,
                     ApprovedAuthorization(*order),
                     MarketQuoteFor(*order),
                     NowMs(),
                     &error);
  broker.Submit(order->id, &error);
  broker.Acknowledge(order->id, &error);
  Quote first = GoodQuote(100.0);
  first.symbol = "AAA";
  first.ask = 100.0;
  first.ask_qty = 2;
  Check(broker.ApplyQuote(order->id, first, NowMs(), &error),
        "first partial fill applied");
  Check(order->filled_quantity == 2, "first partial quantity exact");
  Check(order->cumulative_notional_paise == 20000,
        "first partial notional exact");
  Check(order->average_fill == Money::FromPaise(10000),
        "first average fill exact");
  Quote second = GoodQuote(110.0);
  second.symbol = "AAA";
  second.ask = 110.0;
  second.ask_qty = 2;
  Check(broker.ApplyQuote(order->id, second, NowMs(), &error),
        "second partial fill applied");
  Check(order->filled_quantity == 4, "both fills counted");
  Check(order->cumulative_notional_paise == 42000,
        "cumulative notional is 42000 paise");
  Check(order->average_fill == Money::FromPaise(10500),
        "average fill is 10500 paise, not 5500");
  Check(broker.Reconcile(order->id, &error), "fills reconciled");

  PaperBroker restarted(
      config,
      &journal,
      std::make_shared<Portfolio>(Money::FromRupees(100000.0)));
  Check(restarted.RehydrateFromJournal(&error),
        "restart rebuilds two-fill order");
  const PaperOrder* restored = restarted.Find(order->id);
  Check(restored != nullptr, "restarted order found");
  Check(restored != nullptr && restored->cumulative_notional_paise == 42000,
        "restarted cumulative notional exact");
  Check(
      restored != nullptr && restored->average_fill == Money::FromPaise(10500),
      "restarted average fill matches in-memory value");
  Check(restored != nullptr && restored->fills.size() == 2,
        "both fill lots preserved");
  journal.Close();
}

void TestReplayFillConflicts() {
  const std::string path = TempPath("replay_conflict.jnl");
  std::remove(path.c_str());
  std::string error;
  Journal journal;
  Check(journal.Open(path, &error), "conflict journal opens");
  auto append_order = [&](PaperOrderState state) {
    Json::Object payload;
    payload["order_id"] = quant::Json::String("O-1");
    payload["symbol"] = quant::Json::String("AAA");
    payload["state"] = quant::Json::String(PaperOrderStateName(state));
    payload["side"] = quant::Json::String("BUY");
    payload["quantity"] = quant::Json::Integer(4);
    payload["limit_price_paise"] = quant::Json::Integer(11000);
    payload["filled_quantity"] = quant::Json::Integer(0);
    payload["order_type"] = quant::Json::String("LIMIT");
    payload["time_in_force"] = quant::Json::String("DAY");
    payload["schema_version"] = quant::Json::UInteger(1);
    Event event;
    event.type = EventType::kPaperIntent;
    event.payload = quant::Json::ObjectValue(payload).Dump();
    return journal.Append(&event, &error);
  };
  auto append_fill = [&](const char* id, int quantity, int64_t price) {
    FillRecord record;
    record.id = id;
    record.order_id = "O-1";
    record.symbol = "AAA";
    record.quantity = quantity;
    record.price_paise = price;
    if (!money_detail::CheckedMultiply(price, quantity, &record.notional_paise))
      return false;
    Json::Object payload = FillRecordJson(record);
    payload["order_id"] = quant::Json::String("O-1");
    payload["symbol"] = quant::Json::String("AAA");
    payload["state"] = quant::Json::String("PARTIALLY_FILLED");
    payload["side"] = quant::Json::String("BUY");
    payload["quantity"] = quant::Json::Integer(4);
    payload["limit_price_paise"] = quant::Json::Integer(11000);
    payload["filled_quantity"] = quant::Json::Integer(2);
    payload["order_type"] = quant::Json::String("LIMIT");
    payload["time_in_force"] = quant::Json::String("DAY");
    payload["schema_version"] = quant::Json::UInteger(1);
    Event event;
    event.type = EventType::kPaperIntent;
    event.payload = quant::Json::ObjectValue(payload).Dump();
    return journal.Append(&event, &error);
  };
  Check(append_order(PaperOrderState::kIntent), "conflict order intent");
  Check(append_order(PaperOrderState::kRiskApproved), "risk approval");
  Check(append_order(PaperOrderState::kSubmitted), "submission");
  Check(append_order(PaperOrderState::kAcknowledged), "acknowledgement");
  Check(append_fill("F-SAME", 2, 10000), "first fill recorded");
  Check(append_fill("F-SAME", 2, 10000), "identical retry recorded");
  PaperBroker dedup(TestConfig(),
                    &journal,
                    std::make_shared<Portfolio>(Money::FromRupees(100000.0)));
  Check(dedup.RehydrateFromJournal(&error),
        "identical duplicate fill id is idempotent");
  Check(dedup.Find("O-1")->fills.size() == 1,
        "duplicate fill applies only once");

  const std::string bad_path = TempPath("replay_conflict_bad.jnl");
  std::remove(bad_path.c_str());
  Journal bad;
  bad.Open(bad_path, &error);
  auto append_bad = [&](const char* id, int quantity, int64_t price) {
    FillRecord record;
    record.id = id;
    record.order_id = "O-1";
    record.symbol = "AAA";
    record.quantity = quantity;
    record.price_paise = price;
    money_detail::CheckedMultiply(price, quantity, &record.notional_paise);
    Json::Object payload = FillRecordJson(record);
    payload["order_id"] = quant::Json::String("O-1");
    payload["symbol"] = quant::Json::String("AAA");
    payload["state"] = quant::Json::String("PARTIALLY_FILLED");
    payload["side"] = quant::Json::String("BUY");
    payload["quantity"] = quant::Json::Integer(4);
    payload["limit_price_paise"] = quant::Json::Integer(11000);
    payload["filled_quantity"] = quant::Json::Integer(2);
    payload["order_type"] = quant::Json::String("LIMIT");
    payload["time_in_force"] = quant::Json::String("DAY");
    Event event;
    event.type = EventType::kPaperIntent;
    event.payload = quant::Json::ObjectValue(payload).Dump();
    return bad.Append(&event, &error);
  };
  auto append_bad_order = [&](PaperOrderState state) {
    Json::Object payload;
    payload["order_id"] = quant::Json::String("O-1");
    payload["symbol"] = quant::Json::String("AAA");
    payload["state"] = quant::Json::String(PaperOrderStateName(state));
    payload["side"] = quant::Json::String("BUY");
    payload["quantity"] = quant::Json::Integer(4);
    payload["limit_price_paise"] = quant::Json::Integer(11000);
    payload["filled_quantity"] = quant::Json::Integer(2);
    payload["order_type"] = quant::Json::String("LIMIT");
    payload["time_in_force"] = quant::Json::String("DAY");
    payload["schema_version"] = quant::Json::UInteger(1);
    Event event;
    event.type = EventType::kPaperIntent;
    event.payload = quant::Json::ObjectValue(payload).Dump();
    return bad.Append(&event, &error);
  };
  Check(append_bad_order(PaperOrderState::kIntent), "bad order intent");
  Check(append_bad_order(PaperOrderState::kRiskApproved), "bad risk approval");
  Check(append_bad_order(PaperOrderState::kSubmitted), "bad submission");
  Check(append_bad_order(PaperOrderState::kAcknowledged), "bad ack");
  Check(append_bad("F-CONFLICT", 2, 10000), "conflicting first fill");
  Check(append_bad("F-CONFLICT", 1, 10500), "conflicting duplicate payload");
  PaperBroker strict(TestConfig(),
                     &bad,
                     std::make_shared<Portfolio>(Money::FromRupees(100000.0)));
  Check(!strict.RehydrateFromJournal(&error),
        "conflicting duplicate fill id stops replay");
  Check(!error.empty() &&
            (std::string(error).find("fill") != std::string::npos ||
             std::string(error).find("recorded") != std::string::npos),
        "conflict failure is explicit");
  bad.Close();
  journal.Close();
}

void TestPaperOrderReducer() {
  const std::string path = TempPath("paper_reducer.jnl");
  std::remove(path.c_str());
  std::string error;
  Journal journal;
  Check(journal.Open(path, &error), "reducer journal opens");
  auto portfolio = std::make_shared<Portfolio>(Money::FromRupees(100000.0));
  Config config = TestConfig();
  config.max_participation_fraction = 0.5;
  PaperBroker broker(config, &journal, portfolio);
  PaperOrder* order = broker.CreateIntent(
      "AAA", OrderSide::kBuy, 4, Money::FromRupees(200.0), &error);
  Check(order != nullptr, "order intent created");
  broker.RiskApprove(order->id,
                     ApprovedAuthorization(*order),
                     MarketQuoteFor(*order),
                     NowMs(),
                     &error);
  broker.Submit(order->id, &error);
  broker.Acknowledge(order->id, &error);
  const size_t events_before = journal.Replay(&error).size();
  Quote quote = GoodQuote(100.0);
  quote.symbol = "AAA";
  quote.ask = 100.0;
  quote.ask_qty = 4;
  Check(broker.ApplyQuote(order->id, quote, NowMs(), &error), "fill applied");
  Check(journal.Replay(&error).size() == events_before + 1,
        "fill and state transition recorded in one event");
  Check(broker.Reconcile(order->id, &error), "fill reconciled");

  PaperBroker restarted(
      config,
      &journal,
      std::make_shared<Portfolio>(Money::FromRupees(100000.0)));
  Check(restarted.RehydrateFromJournal(&error), "reducer rehydrates");
  const PaperOrder* restored = restarted.Find(order->id);
  Check(
      restored != nullptr && restored->limit_price == Money::FromRupees(200.0),
      "working order parameters restored");
  Check(restored != nullptr && restored->order_type == "LIMIT" &&
            restored->time_in_force == "DAY",
        "order type and TIF restored");

  Portfolio rebuilt(Money::FromRupees(100000.0));
  for (const Event& event : journal.Replay(&error)) {
    if (event.type != EventType::kAccounting)
      continue;
    AccountingPosting posting;
    Check(ParseAccountingPosting(event, &posting, &error),
          "reducer accounting event parses strictly");
    Check(ApplyAccountingPosting(posting, &rebuilt, &error),
          "reducer posting applies");
  }
  Check(rebuilt.open_position_count() == portfolio->open_position_count() &&
            rebuilt.ledger().cash == portfolio->ledger().cash,
        "fresh portfolio projection matches original ledger");

  const std::string corrupt_path = TempPath("paper_reducer_corrupt.jnl");
  std::remove(corrupt_path.c_str());
  Journal corrupt;
  corrupt.Open(corrupt_path, &error);
  Json::Object payload;
  payload["order_id"] = quant::Json::String("X");
  payload["symbol"] = quant::Json::String("AAA");
  payload["state"] = quant::Json::String("INTENT");
  payload["quantity"] = quant::Json::Integer(1);
  Event bad;
  bad.type = EventType::kPaperIntent;
  bad.payload = Json::ObjectValue(payload).Dump();
  corrupt.Append(&bad, &error);
  PaperBroker strict(config, &corrupt, portfolio);
  Check(!strict.RehydrateFromJournal(&error),
        "incomplete order record fails rehydration closed");
  corrupt.Close();
  journal.Close();
}

Json ValidCandleRow(const std::string& date) {
  return Json::Array({Json::String(date),
                      Json::Number(100.0),
                      Json::Number(103.0),
                      Json::Number(99.0),
                      Json::Number(102.0),
                      Json::Number(1000.0),
                      Json::Number(7.0)});
}

void TestUpstoxQuoteTimestampNormalization() {
  Json::Object item;
  item["last_price"] = Json::Number(100.0);
  item["timestamp"] = Json::String("2026-11-27T10:15:30+05:30");
  item["last_trade_time"] = Json::String("1764219930000");
  Json::Object buy;
  buy["price"] = Json::Number(99.9);
  buy["quantity"] = Json::Number(100.0);
  Json::Object sell;
  sell["price"] = Json::Number(100.1);
  sell["quantity"] = Json::Number(100.0);
  std::vector<Json> buy_levels;
  buy_levels.push_back(Json::ObjectValue(buy));
  std::vector<Json> sell_levels;
  sell_levels.push_back(Json::ObjectValue(sell));
  Json::Object depth;
  depth["buy"] = Json::Array(buy_levels);
  depth["sell"] = Json::Array(sell_levels);
  item["depth"] = Json::ObjectValue(depth);
  const Quote quote =
      NormalizeUpstoxQuote(Json::ObjectValue(item), "AAA", "UPSTOX:AAA");
  Check(quote.provider_timestamp_ms > 0,
        "ISO provider timestamp parsed instead of becoming zero");
  Check(quote.last_trade_timestamp_ms == 1764219930000,
        "last trade millisecond timestamp preserved");
  Check(quote.quality == "ok", "valid timestamp quote accepted");

  Json::Object future = item;
  future["timestamp"] = Json::String("2999-01-01T00:00:00Z");
  const Quote invalid =
      NormalizeUpstoxQuote(Json::ObjectValue(future), "AAA", "UPSTOX:AAA");
  Check(invalid.quality == "invalid_timestamp" &&
            invalid.provider_timestamp_ms == 0,
        "future timestamp quarantined with unknown freshness");

  auto normalized = [&](const Json& value) {
    return NormalizeUpstoxQuote(value, "AAA", "UPSTOX:AAA");
  };
  const int64_t wall_now =
      std::chrono::duration_cast<std::chrono::milliseconds>(
          std::chrono::system_clock::now().time_since_epoch())
          .count();
  Json::Object missing_update = item;
  missing_update.erase("timestamp");
  Check(normalized(Json::ObjectValue(missing_update)).quality ==
            "invalid_timestamp",
        "missing provider timestamp is explicit");
  Json::Object missing_trade = item;
  missing_trade.erase("last_trade_time");
  Check(normalized(Json::ObjectValue(missing_trade)).quality ==
            "invalid_timestamp",
        "missing trade timestamp is explicit under fail-closed policy");
  Json::Object null_update = item;
  null_update["timestamp"] = Json::Null();
  Check(
      normalized(Json::ObjectValue(null_update)).quality == "invalid_timestamp",
      "null timestamp is rejected");
  Json::Object empty_update = item;
  empty_update["timestamp"] = Json::String("");
  Check(normalized(Json::ObjectValue(empty_update)).quality ==
            "invalid_timestamp",
        "empty timestamp is rejected");
  Json::Object numeric_update = item;
  numeric_update["timestamp"] = Json::Integer(wall_now - 1000);
  Check(normalized(Json::ObjectValue(numeric_update)).quality == "ok",
        "numeric millisecond timestamp accepted");
  Json::Object zulu = item;
  zulu["timestamp"] = Json::String("2026-11-27T04:45:30.123Z");
  Check(normalized(Json::ObjectValue(zulu)).quality == "ok",
        "ISO Z timestamp accepted");
  Json::Object positive_offset = item;
  positive_offset["timestamp"] = Json::String("2026-11-27T10:15:30.123+05:30");
  Check(normalized(Json::ObjectValue(positive_offset)).quality == "ok",
        "positive timezone offset accepted");
  Json::Object negative_offset = item;
  negative_offset["timestamp"] = Json::String("2026-11-26T23:15:30.123-05:00");
  Check(normalized(Json::ObjectValue(negative_offset)).quality == "ok",
        "negative timezone offset accepted");
  Json::Object invalid_day = item;
  invalid_day["timestamp"] = Json::String("2026-02-31T10:15:30Z");
  Check(
      normalized(Json::ObjectValue(invalid_day)).quality == "invalid_timestamp",
      "invalid calendar date rejected");
  Json::Object invalid_offset = item;
  invalid_offset["timestamp"] = Json::String("2026-11-27T10:15:30+99:99");
  Check(normalized(Json::ObjectValue(invalid_offset)).quality ==
            "invalid_timestamp",
        "invalid timezone offset rejected");
  Json::Object excess_fraction = item;
  excess_fraction["timestamp"] =
      Json::String("2026-11-27T10:15:30.123456789+05:30");
  Check(normalized(Json::ObjectValue(excess_fraction)).quality ==
            "invalid_timestamp",
        "excess fractional precision rejected instead of silently truncated");
  Json::Object slight_future = item;
  slight_future["timestamp"] = Json::Integer(wall_now + 60000);
  Check(normalized(Json::ObjectValue(slight_future)).quality == "ok",
        "slightly future timestamp inside tolerance accepted");
  Json::Object outside = item;
  outside["timestamp"] = Json::Integer(wall_now + 600000);
  Check(normalized(Json::ObjectValue(outside)).quality == "invalid_timestamp",
        "timestamp outside future tolerance rejected");
}

void TestIsoTimestampFailClosed() {
  Json::Object item;
  item["last_price"] = Json::Number(100.0);
  item["timestamp"] = Json::String("2026-11-27T04:45:30Z");
  item["last_trade_time"] = Json::String("1795754730000");
  Json::Object buy;
  buy["price"] = Json::Number(99.9);
  buy["quantity"] = Json::Number(100.0);
  Json::Object sell;
  sell["price"] = Json::Number(100.1);
  sell["quantity"] = Json::Number(100.0);
  Json::Object depth;
  depth["buy"] = Json::Array({Json::ObjectValue(buy)});
  depth["sell"] = Json::Array({Json::ObjectValue(sell)});
  item["depth"] = Json::ObjectValue(depth);

  auto normalized = [&](const std::string& timestamp) {
    Json::Object candidate = item;
    candidate["timestamp"] = Json::String(timestamp);
    return NormalizeUpstoxQuote(
        Json::ObjectValue(candidate), "AAA", "UPSTOX:AAA");
  };
  auto expect_rejected = [&](const std::string& timestamp) {
    const Quote quote = normalized(timestamp);
    Check(quote.quality == "invalid_timestamp",
          "malformed timestamp rejected: " + timestamp);
    Check(quote.provider_timestamp_ms == 0 &&
              quote.last_trade_timestamp_ms == 0 &&
              quote.exchange_timestamp_ms == 0,
          "rejected timestamp zeroes market times: " + timestamp);
    Check(ProviderFreshnessUnknown(quote) && TradeFreshnessUnknown(quote),
          "rejected timestamp leaves freshness unknown: " + timestamp);
  };
  auto expect_utc = [&](const std::string& timestamp, int64_t expected_ms) {
    const Quote quote = normalized(timestamp);
    Check(quote.quality == "ok",
          "well-formed timestamp accepted: " + timestamp);
    Check(quote.provider_timestamp_ms == expected_ms,
          "UTC conversion exact: " + timestamp);
  };

  const std::vector<std::string> malformed = {
      "2026-00-10T10:15:30Z",      "2026-13-01T10:15:30Z",
      "2026-xx-01T10:15:30Z",      "2026-11-xxT10:15:30Z",
      "2026-11-27Txx:15:30Z",      "2026-11-27T10:xx:30Z",
      "2026-11-27T10:15:xxZ",      "2026-11-27T10:15:30+05x30",
      "2026-11-27T10:15:30+0530",  "2026-11-27T10:15:30.Z",
      "2026-11-27T10:15:30.1234Z", "2026-02-29T10:15:30Z",
      "2024-02-30T10:15:30Z",      "2026-11-27T10:15:60Z",
      "2026-11-27T24:00:00Z",      "2026-11-27T10:60:00Z",
      "2026-11-27T10:15:30Zjunk",  "2026-11-27t10:15:30Z",
      "2026-11-27T10:15:30",       "1969-12-31T23:59:59Z"};
  for (const std::string& timestamp : malformed)
    expect_rejected(timestamp);

  expect_utc("2026-11-27T04:45:30Z", 1795754730000);
  expect_utc("2026-11-27T10:15:30+05:30", 1795754730000);
  expect_utc("2026-11-26T23:15:30-05:00", 1795752930000);
  expect_utc("2026-11-27T04:45:30.5Z", 1795754730500);
  expect_utc("2026-11-27T04:45:30.12Z", 1795754730120);
  expect_utc("2026-11-27T04:45:30z", 1795754730000);
  expect_utc("2024-02-29T10:15:30Z", 1709201730000);

  const Quote received = normalized("2026-11-27T04:45:30Z");
  const int64_t wall_now =
      std::chrono::duration_cast<std::chrono::milliseconds>(
          std::chrono::system_clock::now().time_since_epoch())
          .count();
  Check(received.receive_wall_clock_ms > 0 &&
            wall_now - received.receive_wall_clock_ms < 5000 &&
            received.receive_wall_clock_ms - wall_now < 5000,
        "independent wall-clock receipt recorded in UTC milliseconds");
  Check(received.receive_timestamp_ms > 0 &&
            received.receive_timestamp_ms != received.receive_wall_clock_ms,
        "monotonic receipt distinct from wall-clock receipt");
}

void TestExactIntegerOpenInterest() {
  auto row = [](const Json& oi) {
    return Json::Array({Json::String("2026-01-01"),
                        Json::Number(100.0),
                        Json::Number(103.0),
                        Json::Number(99.0),
                        Json::Number(102.0),
                        Json::Number(1000.0),
                        oi});
  };
  auto converted = [&](const Json& oi) {
    return BuildHistoryCsv(Json::Array({row(oi)}), "AAA");
  };
  const std::pair<uint64_t, const char*> exact[] = {
      {9007199254740991ULL, "9007199254740991"},
      {9007199254740992ULL, "9007199254740992"},
      {9007199254740993ULL, "9007199254740993"},
      {9007199254740994ULL, "9007199254740994"},
      {static_cast<uint64_t>(std::numeric_limits<int64_t>::max()),
       "9223372036854775807"},
  };
  for (const auto& item : exact) {
    const HistoryParseResult result = converted(Json::UInteger(item.first));
    Check(result.errors.empty(),
          std::string("exact integer accepted: ") + item.second);
    Check(result.csv.find(std::string("1000,") + item.second + "\n") !=
              std::string::npos,
          std::string("exact integer preserved: ") + item.second);
  }
  const HistoryParseResult overflow =
      converted(Json::UInteger(9223372036854775808ULL));
  Check(!overflow.errors.empty(),
        "unsigned open interest above INT64_MAX rejected");
  const HistoryParseResult sentinel = converted(Json::Integer(-1));
  Check(sentinel.errors.empty() &&
            sentinel.csv.find("1000,-1\n") != std::string::npos,
        "signed unavailable sentinel preserved");
}

void TestProviderHistoryValidation() {
  std::string error;
  Json valid =
      Json::Array({ValidCandleRow("2026-01-01"), ValidCandleRow("2026-01-02")});
  HistoryParseResult result = BuildHistoryCsv(valid, "AAA");
  Check(result.errors.empty() && result.accepted == 2,
        "valid provider candles accepted");
  Check(result.csv.find("2026-01-01,100") != std::string::npos,
        "valid calendar normalized to CSV");

  HistoryParseResult width =
      BuildHistoryCsv(Json::Array({Json::Array({Json::String("2026-01-01"),
                                                Json::Number(100.0),
                                                Json::Number(103.0),
                                                Json::Number(99.0),
                                                Json::Number(102.0),
                                                Json::Number(1000.0),
                                                Json::Number(7.0),
                                                Json::Number(8.0)})}),
                      "AAA");
  Check(!width.errors.empty() && width.accepted == 0,
        "unexpected eighth field rejected");

  Check(
      result.csv.find("2026-01-01,100,103,99,102,1000,7") != std::string::npos,
      "open interest is preserved in canonical output");
  const HistoryParseResult negative_oi =
      BuildHistoryCsv(Json::Array({Json::Array({Json::String("2026-01-01"),
                                                Json::Number(100.0),
                                                Json::Number(103.0),
                                                Json::Number(99.0),
                                                Json::Number(102.0),
                                                Json::Number(1000.0),
                                                Json::Number(-2.0)})}),
                      "AAA");
  Check(!negative_oi.errors.empty(), "negative open interest rejected");
  const HistoryParseResult string_oi =
      BuildHistoryCsv(Json::Array({Json::Array({Json::String("2026-01-01"),
                                                Json::Number(100.0),
                                                Json::Number(103.0),
                                                Json::Number(99.0),
                                                Json::Number(102.0),
                                                Json::Number(1000.0),
                                                Json::String("7")})}),
                      "AAA");
  Check(!string_oi.errors.empty(), "non-numeric open interest rejected");

  auto with_oi = [](double oi) {
    return Json::Array({Json::Array({Json::String("2026-01-01"),
                                     Json::Number(100.0),
                                     Json::Number(103.0),
                                     Json::Number(99.0),
                                     Json::Number(102.0),
                                     Json::Number(1000.0),
                                     Json::Number(oi)})});
  };
  const HistoryParseResult zero_oi = BuildHistoryCsv(with_oi(0.0), "AAA");
  Check(zero_oi.errors.empty() &&
            zero_oi.csv.find("2026-01-01,100,103,99,102,1000,0\n") !=
                std::string::npos,
        "genuine zero open interest distinct from unavailable sentinel");
  const HistoryParseResult sentinel_oi = BuildHistoryCsv(with_oi(-1.0), "AAA");
  Check(sentinel_oi.errors.empty() &&
            sentinel_oi.csv.find("2026-01-01,100,103,99,102,1000,-1\n") !=
                std::string::npos,
        "unavailable open interest sentinel preserved");
  const HistoryParseResult fractional_oi = BuildHistoryCsv(with_oi(1.5), "AAA");
  Check(!fractional_oi.errors.empty(), "fractional open interest rejected");
  const HistoryParseResult huge_oi = BuildHistoryCsv(with_oi(1.0e300), "AAA");
  Check(!huge_oi.errors.empty(), "out-of-range open interest rejected");
  const HistoryParseResult boundary_oi =
      BuildHistoryCsv(with_oi(9223372036854775808.0), "AAA");
  Check(!boundary_oi.errors.empty(),
        "open interest at 2^63 rejected before integer conversion");
  Json near_max_row = Json::Array({Json::String("2026-01-01"),
                                   Json::Number(100.0),
                                   Json::Number(103.0),
                                   Json::Number(99.0),
                                   Json::Number(102.0),
                                   Json::Number(1000.0),
                                   Json::UInteger(9223372036854774784ULL)});
  const HistoryParseResult near_max_oi =
      BuildHistoryCsv(Json::Array({near_max_row}), "AAA");
  Check(near_max_oi.errors.empty() &&
            near_max_oi.csv.find("9223372036854774784") != std::string::npos,
        "largest exactly representable int64 open interest preserved");
  std::vector<Candle> bounded_candles;
  Check(!ReadCsvText("date,open,high,low,close,volume,open_interest\n"
                     "2026-01-01,100,103,99,102,1000,9223372036854775808\n",
                     &bounded_candles,
                     &error,
                     "v3"),
        "canonical v3 reader rejects out-of-range open interest");
  auto with_volume = [](const Json& volume) {
    return Json::Array({Json::Array({Json::String("2026-01-01"),
                                     Json::Number(100.0),
                                     Json::Number(103.0),
                                     Json::Number(99.0),
                                     Json::Number(102.0),
                                     volume,
                                     Json::Number(7.0)})});
  };
  Check(
      !BuildHistoryCsv(Json::Array({with_volume(Json::Number(1000.5))}), "AAA")
           .errors.empty(),
      "fractional volume rejected");
  Check(!BuildHistoryCsv(
             Json::Array({with_volume(Json::UInteger(9007199254740993ULL))}),
             "AAA")
             .errors.empty(),
        "volume above exact double integer range rejected");
  Check(!ReadCsvText("date,open,high,low,close,volume\n"
                     "2026-01-01,100,103,99,102,1000.5\n",
                     &bounded_candles,
                     &error,
                     "v2"),
        "canonical v2 reader rejects fractional volume");

  HistoryParseResult reversed = BuildHistoryCsv(
      Json::Array({ValidCandleRow("2026-01-02"), ValidCandleRow("2026-01-01")}),
      "AAA");
  Check(reversed.errors.empty() && reversed.accepted == 2 &&
            reversed.csv.find("2026-01-01,100") <
                reversed.csv.find("2026-01-02,100"),
        "newest-first provider history normalized to ascending sessions");

  HistoryParseResult missing =
      BuildHistoryCsv(Json::Array({Json::Array({Json::String("2026-01-01"),
                                                Json::Number(100.0),
                                                Json::Number(103.0),
                                                Json::Number(99.0),
                                                Json::Number(102.0)})}),
                      "AAA");
  Check(!missing.errors.empty(), "short provider row rejected");

  HistoryParseResult non_numeric =
      BuildHistoryCsv(Json::Array({Json::Array({Json::String("2026-01-01"),
                                                Json::Number(100.0),
                                                Json::Number(103.0),
                                                Json::Number(99.0),
                                                Json::String("invalid"),
                                                Json::Number(1000.0)})}),
                      "AAA");
  Check(!non_numeric.errors.empty(), "non-numeric price rejected");

  HistoryParseResult impossible =
      BuildHistoryCsv(Json::Array({ValidCandleRow("2026-02-31")}), "AAA");
  Check(!impossible.errors.empty(), "impossible provider date rejected");

  HistoryParseResult duplicate = BuildHistoryCsv(
      Json::Array({ValidCandleRow("2026-01-01"), ValidCandleRow("2026-01-01")}),
      "AAA");
  Check(!duplicate.errors.empty(), "duplicate provider date rejected");

  Json zero_close = Json::Array({Json::String("2026-01-01"),
                                 Json::Number(100.0),
                                 Json::Number(103.0),
                                 Json::Number(99.0),
                                 Json::Number(0.0),
                                 Json::Number(1000.0)});
  HistoryParseResult zero = BuildHistoryCsv(Json::Array({zero_close}), "AAA");
  Check(!zero.errors.empty(), "zero close never coerced to valid data");
}

void TestInstrumentReferenceModel() {
  ReferenceInstrument equity;
  equity.permanent_id = "PERM-INE001A01DD1";
  equity.venue = "NSE";
  equity.isin = "INE001A01DD1";
  equity.currency = "INR";
  equity.price_scale = 2;
  equity.quantity_scale = 0;
  equity.tick_size_minor = 5;
  equity.lot_size = 1;
  equity.contract_multiplier = 1;
  equity.calendar_id = "NSE-CALENDAR-v1";
  equity.session_model = "NSE-CASH-v1";
  equity.settlement_model = "T_PLUS_1";
  equity.margin_model = "CASH_FULL";
  equity.fee_schedule_id = "ZERODHA-EQ-2026-11-25-v1";
  equity.effective_from_ms = 1;
  std::string error;
  Check(ValidateReferenceInstrument(equity, &error), "reference equity valid");
  Check(VenueConformanceSupported(
            "NSE", AssetClass::kCashEquity, "NSE-CALENDAR-v1"),
        "NSE cash equity conformance available");
  Check(!VenueConformanceSupported(
            "NSE", AssetClass::kDerivative, "NSE-CALENDAR-v1"),
        "derivative conformance not claimed");
  Check(!VenueConformanceSupported(
            "BSE", AssetClass::kCashEquity, "NSE-CALENDAR-v1"),
        "BSE conformance not claimed");
  ReferenceInstrument bad = equity;
  bad.price_scale = -1;
  Check(!ValidateReferenceInstrument(bad, &error), "negative scale rejected");
  bad = equity;
  bad.tick_size_minor = 0;
  Check(!ValidateReferenceInstrument(bad, &error), "zero tick rejected");
  bad = equity;
  bad.currency = "RUPEE";
  Check(!ValidateReferenceInstrument(bad, &error), "invalid currency rejected");
  bad = equity;
  bad.permanent_id.clear();
  Check(!ValidateReferenceInstrument(bad, &error),
        "missing permanent identity rejected");
}

DatasetExpectations TestDatasetExpectations(
    const std::vector<std::string>& symbols,
    const std::vector<std::string>& sessions,
    int64_t min_rows = 1) {
  DatasetExpectations expected;
  expected.snapshot_id = "SNAP-UNIT-TEST";
  expected.calendar_id = "NSE_CASH_DAILY_OBSERVED";
  expected.generated_at_ms = 1769000000000;
  expected.provenance = "UNIT_TEST_FIXTURE";
  expected.symbols = symbols;
  expected.sessions = sessions;
  expected.first_date = sessions.front();
  expected.last_date = sessions.back();
  expected.min_rows = min_rows;
  for (const std::string& symbol : symbols) {
    expected.instrument_ids[symbol] = "ID-" + symbol;
    expected.storage_keys[symbol] =
        DatasetStorageKey(expected.instrument_ids[symbol]);
  }
  return expected;
}

void TestDatasetManifestContract() {
  const std::string dir = TempPath("dataset_manifest_v2");
  std::filesystem::remove_all(dir);
  const std::string csv =
      "date,open,high,low,close,volume\n"
      "2026-01-01,100,103,99,102,1000\n"
      "2026-01-02,102,105,101,104,1200\n";
  std::filesystem::create_directories(dir);
  const DatasetExpectations expected =
      TestDatasetExpectations({"AAA"}, {"2026-01-01", "2026-01-02"});
  std::ofstream(dir + "/" + expected.storage_keys.at("AAA") + ".csv") << csv;
  std::string error;
  Check(VerifyDataset(dir, &error).status == DatasetStatus::kBlocked,
        "dataset without authoritative expectations is blocked");
  Check(!error.empty(), "blocked dataset carries an explicit reason");
  Check(!VerifyDataset(dir, &expected, &error).errors.empty(),
        "missing manifest is blocked");
  Check(WriteDatasetManifest(dir, expected, &error),
        "complete v2 manifest published");
  DatasetVerification result = VerifyDataset(dir, &expected, &error);
  Check(result.status == DatasetStatus::kComplete,
        "complete v2 manifest verified");
  Check(result.file_digests.at("AAA") == Sha256Hex(csv),
        "manifest uses the SHA-256 digest algorithm");

  std::ofstream(dir + "/" + expected.storage_keys.at("AAA") + ".csv")
      << "date,open,high,low,close,volume\n";
  result = VerifyDataset(dir, &expected, &error);
  Check(result.status == DatasetStatus::kFailed && !result.errors.empty(),
        "zero-row dataset is rejected");
  std::ofstream(dir + "/" + expected.storage_keys.at("AAA") + ".csv") << csv;

  DatasetExpectations omitted = expected;
  omitted.sessions = {"2026-01-01"};
  omitted.first_date = omitted.last_date = "2026-01-01";
  Check(VerifyDataset(dir, &omitted, &error).status == DatasetStatus::kBlocked,
        "omitted required session is blocked");
  DatasetExpectations extra = expected;
  extra.symbols = {"AAA", "BBB"};
  extra.instrument_ids["BBB"] = "ID-BBB";
  extra.storage_keys["BBB"] = DatasetStorageKey(extra.instrument_ids["BBB"]);
  Check(VerifyDataset(dir, &extra, &error).status == DatasetStatus::kBlocked,
        "omitted required instrument is blocked");
  DatasetExpectations stale = expected;
  stale.generated_at_ms += 1;
  Check(VerifyDataset(dir, &stale, &error).status == DatasetStatus::kBlocked,
        "stale generation is blocked");

  std::ofstream(dir + "/" + extra.storage_keys.at("BBB") + ".csv") << csv;
  Check(WriteDatasetManifest(dir, extra, &error),
        "two-symbol snapshot published");
  std::filesystem::remove(dir + "/" + extra.storage_keys.at("BBB") + ".csv");
  result = VerifyDataset(dir, &extra, &error);
  Check(result.status == DatasetStatus::kPartial,
        "missing required series is PARTIAL, never COMPLETE");
  Check(VerifyDataset(dir, &expected, &error).status == DatasetStatus::kBlocked,
        "unexpected additional instrument is blocked");

  std::ofstream(dir + "/" + extra.storage_keys.at("BBB") + ".csv") << csv;
  std::ifstream manifest_input(dir + "/manifest.json");
  std::string manifest((std::istreambuf_iterator<char>(manifest_input)),
                       std::istreambuf_iterator<char>());
  const size_t rows_at = manifest.find("\"rows\":");
  manifest.replace(rows_at, 9, "\"rows\":99");
  std::ofstream(dir + "/manifest.json") << manifest;
  Check(VerifyDataset(dir, &extra, &error).status == DatasetStatus::kBlocked,
        "malformed manifest type is blocked");
  std::filesystem::remove_all(dir);
}

void TestEventRuntimeOrdering() {
  EventRuntime runtime(8, QueuePolicy::kReject);
  std::string error;
  Check(runtime.Submit(MakeEnvelope("S1", "AAA", 10, 100), &error),
        "higher queued sequence accepted");
  Check(!runtime.Submit(MakeEnvelope("S2", "AAA", 9, 101), &error),
        "lower queued sequence rejected before enqueue");
  Check(runtime.accepted_sequences().at("AAA") == 10,
        "accepted watermark reserved at enqueue");
  Check(runtime.ProcessOne(&error), "queued event commits");
  Check(runtime.sequences().at("AAA") == 10,
        "committed watermark never regresses");
  Check(runtime.Submit(MakeEnvelope("S3", "BBB", 10, 200), &error),
        "same source sequence on another partition accepted");
  Check(runtime.Submit(MakeEnvelope("S4", "AAA", 12, 102), &error),
        "gap accepted into queue");
  Check(runtime.Abort("S4"), "queued event aborted");
  Check(runtime.accepted_sequences().at("AAA") == 10,
        "abort releases sequence reservation");
  Check(runtime.Submit(MakeEnvelope("S5", "AAA", 11, 103), &error),
        "sequence reusable after abort");
}

void TestPublicationFilesystemBoundary() {
  const std::string path = TempPath("publish_boundary.csv");
  const std::string journal_path = TempPath("publish_boundary.jnl");
  std::remove(path.c_str());
  std::remove(journal_path.c_str());
  std::string error;
  Journal journal;
  Check(journal.Open(journal_path, &error), "boundary journal opens");
  Check(!PublishUniverse(path,
                         "x\n",
                         &journal,
                         "../escape",
                         PublicationFailurePoint::kNone,
                         &error),
        "transaction id with traversal is rejected");
  Check(
      !PublishUniverse(
          path, "x\n", &journal, "A/B", PublicationFailurePoint::kNone, &error),
      "transaction id with separator is rejected");
  Check(
      !PublishUniverse(
          path, "x\n", &journal, "A.B", PublicationFailurePoint::kNone, &error),
      "transaction id with dot segment is rejected");

  const std::string staging = StagingPath(path, "TX-SYM");
  std::filesystem::remove(staging);
  const std::string target = TempPath("publish_symlink_target");
  std::ofstream(target) << "attacker\n";
  std::filesystem::create_symlink(target, staging);
  Check(!PublishUniverse(path,
                         "safe\n",
                         &journal,
                         "TX-SYM",
                         PublicationFailurePoint::kNone,
                         &error),
        "symlink staging replacement is rejected");
  Check(std::filesystem::is_symlink(staging),
        "attacker symlink is left untouched");
  std::filesystem::remove(staging);
  Check(PublishUniverse(path,
                        "safe\n",
                        &journal,
                        "TX-SYM",
                        PublicationFailurePoint::kNone,
                        &error),
        "valid transaction publishes after symlink removal");

  const std::string external = TempPath("publish_external_content");
  std::ofstream(external) << "safe\n";
  const std::string recover_path = TempPath("publish_recover.csv");
  const std::string recover_journal_path = TempPath("publish_recover.jnl");
  std::remove(recover_path.c_str());
  std::remove(recover_journal_path.c_str());
  Journal recover_journal;
  Check(recover_journal.Open(recover_journal_path, &error),
        "recovery journal opens");
  {
    Json::Object payload;
    payload["action"] = quant::Json::String("UNIVERSE_PUBLISH");
    payload["transaction_id"] = quant::Json::String("TX-REC");
    payload["content_digest"] = quant::Json::String(Sha256Hex("safe\n"));
    payload["schema_version"] = quant::Json::UInteger(1);
    payload["records"] = quant::Json::UInteger(5);
    Event event;
    event.type = EventType::kHeartbeat;
    event.payload = quant::Json::ObjectValue(payload).Dump();
    Check(recover_journal.Append(&event, &error), "pending publish recorded");
  }
  const std::string recover_staging = StagingPath(recover_path, "TX-REC");
  std::filesystem::create_symlink(external, recover_staging);
  Check(!RecoverUniversePublication(
            recover_path, &recover_journal, nullptr, &error),
        "staged symlink is rejected during recovery");
  Check(std::filesystem::is_symlink(recover_staging),
        "staged symlink is left intact");
  std::filesystem::remove(recover_staging);
  std::ofstream(recover_staging) << "safe\n";
  Check(!RecoverUniversePublication(
            recover_path,
            &recover_journal,
            nullptr,
            &error,
            PublicationFailurePoint::kAfterRecoveryVerificationBeforeRename),
        "recovery fault before rename is explicit");
  Check(RecoverUniversePublication(
            recover_path, &recover_journal, nullptr, &error),
        "recovery completes after fault is cleared");
  std::ifstream recovered(recover_path);
  std::string recovered_content((std::istreambuf_iterator<char>(recovered)),
                                std::istreambuf_iterator<char>());
  Check(recovered_content == "safe\n", "recovered content exact");
  journal.Close();
  recover_journal.Close();
}

void TestUniverseTransactionConsistency() {
  const std::string path = TempPath("universe_tx.csv");
  const std::string journal_path = TempPath("universe_tx.jnl");
  std::remove(path.c_str());
  std::remove(journal_path.c_str());
  std::string error;
  Journal journal;
  Check(journal.Open(journal_path, &error), "transaction journal opens");
  auto audit = [&](const char* action,
                   const std::string& id,
                   const std::string& digest,
                   size_t size) {
    Json::Object payload;
    payload["action"] = quant::Json::String(action);
    payload["transaction_id"] = quant::Json::String(id);
    payload["content_digest"] = quant::Json::String(digest);
    payload["schema_version"] = quant::Json::UInteger(1);
    payload["records"] = quant::Json::UInteger(static_cast<uint64_t>(size));
    Event event;
    event.type = EventType::kHeartbeat;
    event.payload = quant::Json::ObjectValue(payload).Dump();
    return journal.Append(&event, &error);
  };
  const std::string digest = Sha256Hex("content\n");
  Check(audit("UNIVERSE_PUBLISH", "TX-DUP", digest, 8), "publish recorded");
  Check(audit("UNIVERSE_PUBLISH", "TX-DUP", digest, 8),
        "identical retry recorded");
  std::ofstream(path) << "content\n";
  Check(!RecoverUniversePublication(path, &journal, nullptr, &error),
        "duplicate publish-begin records are rejected");
  Check(std::string(error).find("duplicate publish-begin") != std::string::npos,
        "duplicate publish-begin failure is explicit");

  journal.Close();
  const std::string mismatch_journal_path =
      TempPath("universe_tx_mismatch.jnl");
  std::remove(mismatch_journal_path.c_str());
  Journal mismatch_journal;
  Check(mismatch_journal.Open(mismatch_journal_path, &error),
        "mismatch journal opens");
  auto append_audit = [&](const char* action,
                          const std::string& id,
                          const std::string& value,
                          size_t size) {
    Json::Object payload;
    payload["action"] = quant::Json::String(action);
    payload["transaction_id"] = quant::Json::String(id);
    payload["content_digest"] = quant::Json::String(value);
    payload["schema_version"] = quant::Json::UInteger(1);
    payload["records"] = quant::Json::UInteger(static_cast<uint64_t>(size));
    Event event;
    event.type = EventType::kHeartbeat;
    event.payload = quant::Json::ObjectValue(payload).Dump();
    return mismatch_journal.Append(&event, &error);
  };
  Check(append_audit("UNIVERSE_PUBLISH", "TX-BAD", digest, 8),
        "mismatch fixture publish");
  Check(append_audit("UNIVERSE_PUBLISH_COMMIT", "TX-BAD", "wrong", 8),
        "mismatched commit recorded");
  Check(!RecoverUniversePublication(path, &mismatch_journal, nullptr, &error),
        "mismatched commit digest fails recovery");
  Check(std::string(error).find("commit") != std::string::npos ||
            std::string(error).find("mismatch") != std::string::npos,
        "mismatch failure is explicit");
  mismatch_journal.Close();
}

void TestRiskAuthorizationBinding() {
  const std::string path = TempPath("risk_auth.jnl");
  std::remove(path.c_str());
  std::string error;
  Journal journal;
  Check(journal.Open(path, &error), "risk authorization journal opens");
  auto portfolio = std::make_shared<Portfolio>(Money::FromRupees(100000.0));
  PaperBroker broker(TestConfig(), &journal, portfolio);
  PaperOrder* order = broker.CreateIntent(
      "AAA", OrderSide::kBuy, 2, Money::FromRupees(100.0), &error);
  Check(order != nullptr, "authorization order created");
  const RiskAuthorization good = ApprovedAuthorization(*order);
  Check(good.valid(), "risk engine produces a valid capability");
  const int64_t now = NowMs();

  PaperOrder* other = broker.CreateIntent(
      "BBB", OrderSide::kBuy, 2, Money::FromRupees(100.0), &error);
  Check(other != nullptr, "second order created");
  Check(
      !broker.RiskApprove(other->id, good, MarketQuoteFor(*other), now, &error),
      "authorization bound to another order is rejected");
  Check(
      broker.RiskApprove(order->id, good, MarketQuoteFor(*order), now, &error),
      "authorization accepted for its own order");
  Check(
      !broker.RiskApprove(order->id, good, MarketQuoteFor(*order), now, &error),
      "consumed authorization cannot be replayed");

  PaperOrder* tampered = broker.CreateIntent(
      "CCC", OrderSide::kBuy, 2, Money::FromRupees(100.0), &error);
  Check(tampered != nullptr, "tamper fixture created");
  RiskAuthorization for_tampered = ApprovedAuthorization(*tampered);
  tampered->quantity = 3;
  Check(!broker.RiskApprove(
            tampered->id, for_tampered, MarketQuoteFor(*tampered), now, &error),
        "order mutation invalidates the authorization");
  journal.Close();
}

void TestAmbiguousStateRecovery() {
  const std::string path = TempPath("ambiguous_replay.jnl");
  std::remove(path.c_str());
  std::string error;
  Journal journal;
  Check(journal.Open(path, &error), "ambiguous journal opens");
  auto append = [&](PaperOrderState state, int filled) {
    Json::Object payload;
    payload["order_id"] = quant::Json::String("O-AMB");
    payload["symbol"] = quant::Json::String("AAA");
    payload["state"] = quant::Json::String(PaperOrderStateName(state));
    payload["side"] = quant::Json::String("BUY");
    payload["quantity"] = quant::Json::Integer(4);
    payload["limit_price_paise"] = quant::Json::Integer(10000);
    payload["filled_quantity"] = quant::Json::Integer(filled);
    payload["order_type"] = quant::Json::String("LIMIT");
    payload["time_in_force"] = quant::Json::String("DAY");
    payload["schema_version"] = quant::Json::UInteger(1);
    Event event;
    event.type = EventType::kPaperIntent;
    event.payload = quant::Json::ObjectValue(payload).Dump();
    return journal.Append(&event, &error);
  };
  Check(append(PaperOrderState::kIntent, 0), "ambiguous intent");
  Check(append(PaperOrderState::kRiskApproved, 0), "ambiguous risk approval");
  Check(append(PaperOrderState::kSubmitted, 0), "ambiguous submission");
  Check(append(PaperOrderState::kAcknowledged, 0), "ambiguous ack");
  Check(append(PaperOrderState::kAmbiguous, 0), "ambiguous state recorded");
  auto portfolio = std::make_shared<Portfolio>(Money::FromRupees(100000.0));
  PaperBroker broker(TestConfig(), &journal, portfolio);
  Check(broker.RehydrateFromJournal(&error),
        "zero-fill ambiguous order rehydrates");
  const PaperOrder* restored = broker.Find("O-AMB");
  Check(restored != nullptr && restored->state == PaperOrderState::kAmbiguous,
        "ambiguous state is preserved");
  Check(!broker.Submit("O-AMB", &error), "ambiguous order cannot be submitted");
  Check(!broker.Cancel("O-AMB", &error), "ambiguous order cannot be cancelled");
  Check(!broker.Expire("O-AMB", &error), "ambiguous order cannot be expired");
  Check(broker.ResolveAmbiguous("O-AMB",
                                PaperOrderState::kCancelled,
                                "OPERATOR-1",
                                "no broker confirmation",
                                &error),
        "operator resolves ambiguous order");
  Check(broker.Find("O-AMB")->state == PaperOrderState::kCancelled,
        "resolution produces cancelled state");

  const std::string partial_path = TempPath("ambiguous_partial.jnl");
  std::remove(partial_path.c_str());
  Journal partial;
  partial.Open(partial_path, &error);
  auto append_to = [&](Journal* target, PaperOrderState state, int filled) {
    Json::Object payload;
    payload["order_id"] = quant::Json::String("O-AMB2");
    payload["symbol"] = quant::Json::String("AAA");
    payload["state"] = quant::Json::String(PaperOrderStateName(state));
    payload["side"] = quant::Json::String("BUY");
    payload["quantity"] = quant::Json::Integer(4);
    payload["limit_price_paise"] = quant::Json::Integer(10000);
    payload["filled_quantity"] = quant::Json::Integer(filled);
    payload["order_type"] = quant::Json::String("LIMIT");
    payload["time_in_force"] = quant::Json::String("DAY");
    if (filled > 0) {
      FillRecord lot;
      lot.version = 2;
      lot.id = "F-AMB";
      lot.order_id = "O-AMB2";
      lot.symbol = "AAA";
      lot.quantity = filled;
      lot.price_paise = 10000;
      lot.notional_paise = 10000 * filled;
      for (const auto& [key, value] : FillRecordJson(lot))
        payload[key] = value;
    }
    Event event;
    event.type = EventType::kPaperIntent;
    event.payload = quant::Json::ObjectValue(payload).Dump();
    return target->Append(&event, &error);
  };
  Check(append_to(&partial, PaperOrderState::kIntent, 0), "partial intent");
  Check(append_to(&partial, PaperOrderState::kAmbiguous, 2),
        "partially filled ambiguous state");
  PaperBroker partial_broker(
      TestConfig(),
      &partial,
      std::make_shared<Portfolio>(Money::FromRupees(100000.0)));
  const bool partial_ok = partial_broker.RehydrateFromJournal(&error);
  Check(partial_ok, "partially filled ambiguous order rehydrates");
  const PaperOrder* amb = partial_broker.Find("O-AMB2");
  Check(amb != nullptr, "ambiguous order exists");
  if (amb == nullptr) {
    journal.Close();
    partial.Close();
    return;
  }
  Check(amb->state == PaperOrderState::kAmbiguous,
        "partial ambiguous state is not upgraded");
  Check(!partial_broker.ApplyQuote("O-AMB2", GoodQuote(100.0), NowMs(), &error),
        "ambiguous order cannot receive fills");
  journal.Close();
  partial.Close();
}

void TestImpossibleFilledStateReplay() {
  const std::string path = TempPath("filled_zero_fills.jnl");
  std::remove(path.c_str());
  std::string error;
  Journal journal;
  Check(journal.Open(path, &error), "impossible state journal opens");
  auto append = [&](PaperOrderState state, int filled) {
    Json::Object payload;
    payload["order_id"] = quant::Json::String("O-IMPOSSIBLE");
    payload["symbol"] = quant::Json::String("AAA");
    payload["state"] = quant::Json::String(PaperOrderStateName(state));
    payload["side"] = quant::Json::String("BUY");
    payload["quantity"] = quant::Json::Integer(4);
    payload["limit_price_paise"] = quant::Json::Integer(10000);
    payload["filled_quantity"] = quant::Json::Integer(filled);
    payload["order_type"] = quant::Json::String("LIMIT");
    payload["time_in_force"] = quant::Json::String("DAY");
    payload["schema_version"] = quant::Json::UInteger(1);
    Event event;
    event.type = EventType::kPaperIntent;
    event.payload = quant::Json::ObjectValue(payload).Dump();
    return journal.Append(&event, &error);
  };
  Check(append(PaperOrderState::kIntent, 0), "intent recorded");
  Check(append(PaperOrderState::kRiskApproved, 0), "risk approval recorded");
  Check(append(PaperOrderState::kSubmitted, 0), "submission recorded");
  Check(append(PaperOrderState::kAcknowledged, 0), "acknowledgement recorded");
  Check(append(PaperOrderState::kFilled, 4),
        "impossible filled event recorded");
  PaperBroker strict(TestConfig(),
                     &journal,
                     std::make_shared<Portfolio>(Money::FromRupees(100000.0)));
  Check(!strict.RehydrateFromJournal(&error),
        "FILLED without executions fails replay");
  Check(!error.empty(), "impossible state rejection has an explicit reason");
  Check(strict.Find("O-IMPOSSIBLE") == nullptr,
        "impossible order is not projected");
  journal.Close();
}

void TestCancelAfterPartialReplay() {
  const std::string path = TempPath("cancel_partial.jnl");
  std::remove(path.c_str());
  std::string error;
  Journal journal;
  Check(journal.Open(path, &error), "cancel journal opens");
  Config config = TestConfig();
  config.max_participation_fraction = 1.0;
  PaperBroker broker(config,
                     &journal,
                     std::make_shared<Portfolio>(Money::FromRupees(100000.0)));
  PaperOrder* order = broker.CreateIntent(
      "AAA", OrderSide::kBuy, 4, Money::FromRupees(100.0), &error);
  broker.RiskApprove(order->id,
                     ApprovedAuthorization(*order),
                     MarketQuoteFor(*order),
                     NowMs(),
                     &error);
  broker.Submit(order->id, &error);
  broker.Acknowledge(order->id, &error);
  Quote quote = GoodQuote(100.0);
  quote.symbol = "AAA";
  quote.ask = 100.0;
  quote.ask_qty = 2;
  Check(broker.ApplyQuote(order->id, quote, NowMs(), &error),
        "partial fill before cancel");
  Check(order->state == PaperOrderState::kPartiallyFilled,
        "order is partially filled");
  Check(broker.Cancel(order->id, &error), "partially filled order cancels");

  PaperBroker restarted(
      config,
      &journal,
      std::make_shared<Portfolio>(Money::FromRupees(100000.0)));
  Check(restarted.RehydrateFromJournal(&error),
        "cancel-after-partial replay succeeds");
  const PaperOrder* restored = restarted.Find(order->id);
  Check(restored != nullptr, "cancelled order restored");
  Check(restored != nullptr && restored->state == PaperOrderState::kCancelled,
        "cancelled state preserved after restart");
  Check(restored != nullptr && restored->fills.size() == 1,
        "partial fill preserved through cancellation");
  Check(restored != nullptr && restored->cumulative_notional_paise == 20000,
        "partial notional preserved through cancellation");
  journal.Close();
}

void TestMalformedFillReplay() {
  const std::string path = TempPath("malformed_fill.jnl");
  std::remove(path.c_str());
  std::string error;
  Journal journal;
  Check(journal.Open(path, &error), "malformed journal opens");
  auto append = [&](const Json::Object& payload) {
    Event event;
    event.type = EventType::kPaperIntent;
    event.payload = quant::Json::ObjectValue(payload).Dump();
    return journal.Append(&event, &error);
  };
  auto order_event = [](PaperOrderState state) {
    Json::Object payload;
    payload["order_id"] = quant::Json::String("O-1");
    payload["symbol"] = quant::Json::String("AAA");
    payload["state"] = quant::Json::String(PaperOrderStateName(state));
    payload["side"] = quant::Json::String("BUY");
    payload["quantity"] = quant::Json::Integer(4);
    payload["limit_price_paise"] = quant::Json::Integer(15000);
    payload["filled_quantity"] = quant::Json::Integer(0);
    payload["order_type"] = quant::Json::String("LIMIT");
    payload["time_in_force"] = quant::Json::String("DAY");
    payload["schema_version"] = quant::Json::UInteger(1);
    return payload;
  };
  Check(append(order_event(PaperOrderState::kIntent)), "intent for malformed");
  Check(append(order_event(PaperOrderState::kRiskApproved)), "risk approval");
  Check(append(order_event(PaperOrderState::kSubmitted)), "submission");
  Check(append(order_event(PaperOrderState::kAcknowledged)), "ack");
  FillRecord wrong;
  wrong.id = "F-BAD";
  wrong.order_id = "O-1";
  wrong.symbol = "AAA";
  wrong.quantity = 2;
  wrong.price_paise = 10000;
  wrong.notional_paise = 19999;  // Not equal to quantity times price.
  Json::Object fill = FillRecordJson(wrong);
  fill["order_id"] = quant::Json::String("O-1");
  fill["symbol"] = quant::Json::String("AAA");
  fill["state"] = quant::Json::String("PARTIALLY_FILLED");
  fill["side"] = quant::Json::String("BUY");
  fill["quantity"] = quant::Json::Integer(4);
  fill["limit_price_paise"] = quant::Json::Integer(15000);
  Check(append(fill), "notional mismatch event recorded");
  PaperBroker strict(TestConfig(),
                     &journal,
                     std::make_shared<Portfolio>(Money::FromRupees(100000.0)));
  Check(!strict.RehydrateFromJournal(&error), "notional mismatch stops replay");
  Check(!error.empty(), "notional mismatch has an explicit reason");
  journal.Close();
}

void TestRiskAuthorizationContext() {
  const std::string path = TempPath("risk_context.jnl");
  std::remove(path.c_str());
  std::string error;
  Journal journal;
  Check(journal.Open(path, &error), "risk context journal opens");
  auto state = std::make_shared<DailyRiskState>();
  auto portfolio = std::make_shared<Portfolio>(Money::FromRupees(100000.0));
  PaperBroker broker(TestConfig(), &journal, portfolio, state);
  PaperOrder* order = broker.CreateIntent(
      "AAA", OrderSide::kBuy, 2, Money::FromRupees(100.0), &error);
  Check(order != nullptr, "context order created");
  order->instrument_key = "INSTRUMENT-AAA";

  Config config = TestConfig();
  config.max_participation_fraction = 1.0;
  RiskEngine authority(config, state);
  Signal wrong_signal;
  wrong_signal.symbol = "BBB";
  wrong_signal.permanent_id = "INSTRUMENT-BBB";
  wrong_signal.tradable = true;
  Quote wrong_quote = GoodQuote(100.0);
  wrong_quote.symbol = "BBB";
  wrong_quote.provider_key = "INSTRUMENT-BBB";
  const RiskAuthorization mismatched = authority.Authorize(
      wrong_signal, wrong_quote, *portfolio, *order, NowMs());
  Check(!mismatched.valid(),
        "authorization from another instrument is refused at issuance");

  Signal signal;
  signal.symbol = "AAA";
  signal.permanent_id = "INSTRUMENT-AAA";
  signal.tradable = true;
  Quote quote = GoodQuote(100.0);
  quote.symbol = "AAA";
  quote.permanent_id = "INSTRUMENT-AAA";
  quote.provider_key = "INSTRUMENT-AAA";
  quote.ask = 100.0;
  quote.bid = 99.9;
  quote.last = 100.0;
  quote.ask_qty = 1000000;
  quote.bid_qty = 1000000;
  const int64_t now = NowMs();
  const RiskAuthorization valid =
      authority.Authorize(signal, quote, *portfolio, *order, now);
  Check(valid.valid(), "matching context issues authorization");

  state->policy_version = "RISK-POLICY-CHANGED";
  Check(!broker.RiskApprove(order->id, valid, quote, now, &error),
        "policy change revokes outstanding authorization");
  Check(std::string(error).find("policy") != std::string::npos,
        "policy revocation reason is explicit");
  state->policy_version = "RISK-2026-11-26-v1";

  state->journal_sequence += 1;
  Check(!broker.RiskApprove(order->id, valid, quote, now, &error),
        "state revision change revokes outstanding authorization");
  state->journal_sequence -= 1;

  state->halted = true;
  state->halt_reason = "test kill switch";
  Check(!broker.RiskApprove(order->id, valid, quote, now, &error),
        "kill switch revokes outstanding authorization");
  state->halted = false;

  Quote changed = quote;
  changed.sequence = quote.sequence + 1;
  const RiskAuthorization second =
      authority.Authorize(signal, changed, *portfolio, *order, NowMs());
  Check(second.valid(), "second authorization issued");
  Check(broker.RiskApprove(order->id, second, changed, NowMs(), &error),
        "current authorization accepted");

  PaperBroker restarted(TestConfig(), &journal, portfolio, state);
  Check(restarted.RehydrateFromJournal(&error),
        "consumption record survives restart");
  Check(!restarted.RiskApprove(order->id, second, changed, NowMs(), &error),
        "consumed authorization cannot be replayed after restart");

  PaperOrder* venue_order = broker.CreateIntent(
      "CCC", OrderSide::kBuy, 1, Money::FromRupees(100.0), &error);
  venue_order->instrument_key = "INSTRUMENT-CCC";
  Quote venue_quote = MarketQuoteFor(*venue_order);
  Signal venue_signal;
  venue_signal.symbol = "CCC";
  venue_signal.permanent_id = "INSTRUMENT-CCC";
  venue_signal.tradable = true;
  const RiskAuthorization venue_auth = authority.Authorize(
      venue_signal, venue_quote, *portfolio, *venue_order, NowMs());
  Check(venue_auth.valid(), "venue-bound authorization issued");
  venue_order->venue = "BSE";
  Check(!broker.RiskApprove(
            venue_order->id, venue_auth, venue_quote, NowMs(), &error),
        "venue mutation after issuance is rejected");
  venue_order->venue = "NSE";

  Signal missing_signal = venue_signal;
  missing_signal.permanent_id.clear();
  Check(!authority
             .Authorize(
                 missing_signal, venue_quote, *portfolio, *venue_order, NowMs())
             .valid(),
        "missing signal identity fails closed");
  Quote missing_quote = venue_quote;
  missing_quote.permanent_id.clear();
  Check(!authority
             .Authorize(
                 venue_signal, missing_quote, *portfolio, *venue_order, NowMs())
             .valid(),
        "missing quote identity fails closed");
  Check(!broker.RiskApprove(
            venue_order->id, venue_auth, missing_quote, NowMs(), &error),
        "market snapshot with missing identity is rejected");
  journal.Close();
}

void TestPaperRiskAndFreshness() {
  const std::string path = TempPath("paper_fresh.jnl");
  std::remove(path.c_str());
  std::string error;
  Journal journal;
  journal.Open(path, &error);
  auto portfolio = std::make_shared<Portfolio>(Money::FromRupees(100000.0));
  PaperBroker broker(TestConfig(), &journal, portfolio);
  PaperOrder* order = broker.CreateIntent(
      "AAA", OrderSide::kBuy, 2, Money::FromRupees(100.0), &error);
  Check(order != nullptr, "freshness order created");
  Config authority_config = TestConfig();
  authority_config.max_participation_fraction = 1.0;
  auto state = std::make_shared<DailyRiskState>();
  RiskEngine authority(authority_config, state);
  Portfolio deep(Money::FromRupees(100000000.0));
  Signal signal;
  signal.permanent_id = "INSTRUMENT-TEST";
  signal.symbol = "AAA";
  signal.tradable = true;
  Quote quote = GoodQuote(100.0);
  quote.symbol = "AAA";
  quote.ask = 100.0;
  quote.bid = 99.9;
  quote.last = 100.0;
  quote.ask_qty = 1000000;
  quote.bid_qty = 1000000;
  const int64_t now = NowMs();
  const RiskAuthorization expired =
      authority.Authorize(signal, quote, deep, *order, now - 600000);
  Check(!broker.RiskApprove(
            order->id, expired, MarketQuoteFor(*order), now, &error),
        "expired authorization is rejected");
  Check(broker.RiskApprove(order->id,
                           ApprovedAuthorization(*order),
                           MarketQuoteFor(*order),
                           NowMs(),
                           &error),
        "approved risk decision accepted");
  broker.Submit(order->id, &error);
  broker.Acknowledge(order->id, &error);
  Quote stale = GoodQuote(100.0);
  stale.symbol = "AAA";
  stale.provider_timestamp_ms = NowMs() - 600000;
  stale.exchange_timestamp_ms = stale.provider_timestamp_ms;
  stale.last_trade_timestamp_ms = stale.provider_timestamp_ms;
  Check(!broker.ApplyQuote(order->id, stale, NowMs(), &error),
        "stale quote cannot create a fill");

  const std::string bad_path = TempPath("paper_badstate.jnl");
  std::remove(bad_path.c_str());
  Journal bad_journal;
  bad_journal.Open(bad_path, &error);
  Json::Object payload;
  payload["order_id"] = quant::Json::String("X");
  payload["symbol"] = quant::Json::String("AAA");
  payload["state"] = quant::Json::String("UNKNOWN_STATE");
  payload["quantity"] = quant::Json::Integer(1);
  payload["side"] = quant::Json::String("BUY");
  payload["limit_price_paise"] = quant::Json::Integer(10000);
  Event event;
  event.type = EventType::kPaperIntent;
  event.payload = quant::Json::ObjectValue(payload).Dump();
  bad_journal.Append(&event, &error);
  PaperBroker strict(TestConfig(), &bad_journal, portfolio);
  Check(!strict.RehydrateFromJournal(&error),
        "unknown journal state fails rehydration closed");
  bad_journal.Close();
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
  signal.permanent_id = "INSTRUMENT-TEST";
  signal.symbol = "TEST";
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
  signal.permanent_id = "INSTRUMENT-TEST";
  signal.symbol = "TEST";
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
    last_quote = quote;
    saw_quote = true;
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
  Quote last_quote;
  bool saw_quote = false;
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

void TestWallClockJournalReplay() {
  const std::string path = TempPath("wall_clock_replay.jnl");
  std::remove(path.c_str());
  std::string error;
  Journal journal;
  Check(journal.Open(path, &error), "wall clock journal opens");
  MarketDataPipeline pipeline(&journal);
  Quote quote = GoodQuote(100.0);
  quote.symbol = "AAA";
  quote.provider_key = "UPSTOX:AAA";
  quote.exchange_timestamp_ms = 1795754730000;
  quote.provider_timestamp_ms = 1795754731000;
  quote.last_trade_timestamp_ms = 1795754730000;
  quote.receive_timestamp_ms = 4242;
  quote.receive_wall_clock_ms = 1795754730123;
  Check(pipeline.IngestQuote(quote, &error),
        "quote with wall clock receipt journalled");
  journal.Close();

  Journal reopened;
  Check(reopened.Open(path, &error), "journal reopened after close");
  MarketDataPipeline replay(&reopened);
  RecordingPublisher publisher;
  const PipelineResult result =
      replay.Publish(KdbxCheckpoint(), &publisher, &error);
  Check(result.status == PipelineStatus::kComplete && publisher.saw_quote,
        "quote replayed after journal reopen");
  const Quote& restored = publisher.last_quote;
  Check(restored.receive_wall_clock_ms == 1795754730123,
        "wall clock receipt survives journal round trip");
  Check(restored.provider_timestamp_ms == 1795754731000 &&
            restored.exchange_timestamp_ms == 1795754730000 &&
            restored.last_trade_timestamp_ms == 1795754730000,
        "market event timestamps unchanged by replay");
  Check(restored.receive_timestamp_ms == 4242 &&
            restored.receive_timestamp_ms != restored.receive_wall_clock_ms,
        "monotonic receipt preserved and not presented as UTC");
  reopened.Close();

  const std::string legacy_path = TempPath("wall_clock_legacy.jnl");
  std::remove(legacy_path.c_str());
  Journal legacy;
  Check(legacy.Open(legacy_path, &error), "legacy journal opens");
  Json::Object payload;
  payload["symbol"] = Json::String("AAA");
  payload["provider_key"] = Json::String("UPSTOX:AAA");
  payload["source"] = Json::Number(static_cast<int>(Provider::kUpstox));
  payload["last"] = Json::Number(100.0);
  payload["bid"] = Json::Number(99.9);
  payload["ask"] = Json::Number(100.1);
  payload["bid_qty"] = Json::Integer(1000);
  payload["ask_qty"] = Json::Integer(1000);
  payload["volume"] = Json::Number(1000.0);
  payload["exchange_timestamp_ms"] = Json::Integer(1795754730000);
  payload["provider_timestamp_ms"] = Json::Integer(1795754731000);
  payload["last_trade_timestamp_ms"] = Json::Integer(1795754730000);
  payload["receive_timestamp_ms"] = Json::Integer(4242);
  payload["quality"] = Json::String("ok");
  Event legacy_event;
  legacy_event.type = EventType::kQuote;
  legacy_event.payload = Json::ObjectValue(payload).Dump();
  Check(legacy.Append(&legacy_event, &error),
        "legacy quote without wall clock appended");
  legacy.Close();
  Journal legacy_reopened;
  Check(legacy_reopened.Open(legacy_path, &error), "legacy journal reopens");
  MarketDataPipeline legacy_replay(&legacy_reopened);
  RecordingPublisher legacy_publisher;
  const PipelineResult legacy_result =
      legacy_replay.Publish(KdbxCheckpoint(), &legacy_publisher, &error);
  Check(legacy_result.status == PipelineStatus::kComplete &&
            legacy_publisher.saw_quote,
        "legacy quote still replays");
  Check(legacy_publisher.last_quote.receive_wall_clock_ms == 0,
        "legacy event restores unknown wall clock as zero");
  legacy_reopened.Close();
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
  Check(broker.RiskApprove(order->id,
                           ApprovedAuthorization(*order),
                           MarketQuoteFor(*order),
                           NowMs(),
                           &error) &&
            broker.Submit(order->id, &error) &&
            broker.Acknowledge(order->id, &error),
        "partial-fill order working");
  Quote first = GoodQuote(100.0);
  first.symbol = "AAA";
  first.ask = 100.0;
  first.ask_qty = 4;
  Check(broker.ApplyQuote(order->id, first, NowMs(), &error),
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
  Check(broker.ApplyQuote(order->id, second, NowMs(), &error),
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
  Check(broker.RiskApprove(intent->id,
                           ApprovedAuthorization(*intent),
                           MarketQuoteFor(*intent),
                           NowMs(),
                           &error),
        "risk approves intent");
  Check(broker.Submit(intent->id, &error), "approved order submitted");
  Check(broker.Acknowledge(intent->id, &error), "submission acknowledged");

  Quote above_limit = GoodQuote(100.0);
  above_limit.symbol = "AAA";
  above_limit.ask = 105.0;
  Check(broker.ApplyQuote(intent->id, above_limit, NowMs(), &error),
        "quote above limit is accepted without fill");
  Check(intent->filled_quantity == 0, "no fill above limit");
  Quote no_depth = above_limit;
  no_depth.ask = 100.0;
  no_depth.ask_qty = 0;
  Check(broker.ApplyQuote(intent->id, no_depth, NowMs(), &error),
        "touch without depth accepted");
  Check(intent->filled_quantity == 0, "limit touch without depth is no fill");

  Quote executable = no_depth;
  executable.ask_qty = 6;
  Check(broker.ApplyQuote(intent->id, executable, NowMs(), &error),
        "executable quote processed");
  Check(
      intent->filled_quantity > 0 && intent->filled_quantity < intent->quantity,
      "depth-capped partial fill");
  Check(intent->state == PaperOrderState::kPartiallyFilled,
        "partial fill state recorded");
  Check(portfolio->open_position_count() == 0,
        "fills are not posted before reconciliation");
  Check(broker.ApplyQuote(intent->id, executable, NowMs(), &error),
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
  Check(!broker.RiskApprove(intent->id,
                            ApprovedAuthorization(*intent),
                            MarketQuoteFor(*intent),
                            NowMs(),
                            &error),
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
  Check(broker.RiskApprove(cancelled->id,
                           ApprovedAuthorization(*cancelled),
                           MarketQuoteFor(*cancelled),
                           NowMs(),
                           &error) &&
            broker.Submit(cancelled->id, &error) &&
            broker.Acknowledge(cancelled->id, &error),
        "cancel fixture working");
  Check(broker.Cancel(cancelled->id, &error), "working order cancelled");
  Check(cancelled->state == PaperOrderState::kCancelled,
        "cancelled state recorded");

  PaperOrder* ambiguous = broker.CreateIntent(
      "BBB", OrderSide::kBuy, 1, Money::FromRupees(10.0), &error);
  Check(broker.RiskApprove(ambiguous->id,
                           ApprovedAuthorization(*ambiguous),
                           MarketQuoteFor(*ambiguous),
                           NowMs(),
                           &error) &&
            broker.Submit(ambiguous->id, &error) &&
            broker.Acknowledge(ambiguous->id, &error),
        "ambiguous fixture working");
  Check(broker.MarkAmbiguous(ambiguous->id, &error),
        "ambiguous outcome recorded");
  Check(!broker.Reconcile(ambiguous->id, &error),
        "ambiguous order cannot be auto-reconciled");
  PaperOrder* expiring = broker.CreateIntent(
      "BBB", OrderSide::kBuy, 1, Money::FromRupees(10.0), &error);
  Check(broker.RiskApprove(expiring->id,
                           ApprovedAuthorization(*expiring),
                           MarketQuoteFor(*expiring),
                           NowMs(),
                           &error) &&
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
  signal.permanent_id = "INSTRUMENT-TEST";
  signal.symbol = "AAA";
  signal.tradable = false;
  signal.rejection_reason = "test";
  Quote quote;
  quote.symbol = "AAA";
  quote.permanent_id = "INSTRUMENT-TEST";
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
  TestPaperRiskAndFreshness();
  TestRiskAuthorizationBinding();
  TestRiskAuthorizationContext();
  TestCancelAfterPartialReplay();
  TestImpossibleFilledStateReplay();
  TestAmbiguousStateRecovery();
  TestMalformedFillReplay();
  TestUniverseTransactionConsistency();
  TestPublicationFilesystemBoundary();
  TestEventRuntimeOrdering();
  TestDatasetManifestContract();
  TestInstrumentReferenceModel();
  TestProviderHistoryValidation();
  TestExactIntegerOpenInterest();
  TestUpstoxQuoteTimestampNormalization();
  TestIsoTimestampFailClosed();
  TestPaperOrderReducer();
  TestFillAverageArithmetic();
  TestReplayFillConflicts();
  TestAssetAndComplianceContracts();
  TestPaperRehydration();
  TestDeterministicEventRuntime();
  TestUniversePublishRecovery();
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
  TestWallClockJournalReplay();
  std::cout << "checks=" << g_checks << " failures=" << g_failures << "\n";
  return g_failures == 0 ? 0 : 1;
}
