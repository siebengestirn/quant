#include "quant.hpp"

#include <fcntl.h>
#include <netinet/in.h>
#include <signal.h>
#include <spawn.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

#include <cctype>
#include <array>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <set>
#include <sstream>
#include <thread>

extern char** environ;  // NOLINT: required by posix_spawn.

namespace quant {
namespace {

const char* kConfigPath = "config.toml";

struct App {
  Config config;
  Journal journal;
  bool ready = false;
};

App Initialize(std::string* error) {
  App app;
  const ConfigResult loaded = LoadConfig(kConfigPath);
  if (!loaded.ok()) {
    *error = "config invalid: ";
    for (const std::string& item : loaded.errors) *error += item + "; ";
    return app;
  }
  app.config = loaded.config;
  std::string journal_error;
  app.journal.Open(app.config.journal_path, &journal_error);
  if (!journal_error.empty()) {
    *error = journal_error;
    return app;
  }
  app.ready = true;
  return app;
}

std::string NowUtc() {
  const auto now = std::chrono::system_clock::now();
  const std::time_t time = std::chrono::system_clock::to_time_t(now);
  std::tm utc{};
  gmtime_r(&time, &utc);
  std::ostringstream oss;
  oss << std::put_time(&utc, "%Y-%m-%dT%H:%M:%SZ");
  return oss.str();
}

void PrintHelp() {
  std::cout
      << "QUANT commands:\n"
      << "  doctor            environment, config, credentials and feeds\n"
      << "  validate          configuration and compliance validation\n"
      << "  db status|start|stop|verify|benchmark   KDB-X/q integration\n"
      << "  brokers status    provider entitlements (no secrets printed)\n"
      << "  data sync         download BOD universe and daily history\n"
      << "  universe scan     summarise the cached dynamic universe\n"
      << "  market report     next-session research report from real data\n"
      << "  research          run deterministic backtest on cached history\n"
      << "  backtest          alias for research\n"
      << "  research verify   statistical verification and multiple testing\n"
      << "  replay            verify journal integrity and replay events\n"
      << "  paper start       one simulated pass; never places real orders\n"
      << "  portfolio         event-sourced ledger state\n"
      << "  report            print the most recent generated report\n";
}

std::string CaptureCommand(const std::string& command) {
  std::array<char, 1024> buffer{};
  std::string output;
  FILE* pipe = popen(command.c_str(), "r");
  if (pipe == nullptr) return "";
  while (fgets(buffer.data(), static_cast<int>(buffer.size()), pipe) !=
         nullptr) {
    output += buffer.data();
  }
  pclose(pipe);
  return output;
}

std::string QBinary() {
  const char* qhome = std::getenv("QHOME");
  if (qhome != nullptr) {
    const std::string candidate = std::string(qhome) + "/bin/q";
    if (std::system(("test -x '" + candidate + "'").c_str()) == 0) {
      return candidate;
    }
  }
  return "q";
}

bool QProcessAlive(const std::string& pid_path) {
  std::ifstream input(pid_path);
  std::string text;
  if (!input.is_open() || !std::getline(input, text)) return false;
  for (char c : text) {
    if (!std::isdigit(static_cast<unsigned char>(c))) return false;
  }
  if (text.empty()) return false;
  return std::system(("kill -0 " + text + " 2>/dev/null").c_str()) == 0;
}

struct IsolatedQ {
  pid_t pid = -1;
  KdbxConfig config;
  std::string directory;
  std::string instance_id;
  bool owned = false;
};

int FreeLoopbackPort() {
  const int fd = socket(AF_INET, SOCK_STREAM, 0);
  if (fd < 0) return -1;
  sockaddr_in address{};
  address.sin_family = AF_INET;
  address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  address.sin_port = 0;
  if (bind(fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)) != 0) {
    close(fd);
    return -1;
  }
  sockaddr_in assigned{};
  socklen_t length = sizeof(assigned);
  const bool ok =
      getsockname(fd, reinterpret_cast<sockaddr*>(&assigned), &length) == 0;
  close(fd);
  return ok ? ntohs(assigned.sin_port) : -1;
}

bool StartIsolatedQ(const Config& config, IsolatedQ* out, std::string* error) {
  const int port = FreeLoopbackPort();
  if (port < 1) {
    *error = "cannot allocate a loopback port";
    return false;
  }
  std::filesystem::create_directories(std::filesystem::path(config.data_dir) /
                                      "db");
  std::string pattern =
      (std::filesystem::path(config.data_dir) / "db" / "test-XXXXXX").string();
  std::vector<char> buffer(pattern.begin(), pattern.end());
  buffer.push_back('\0');
  if (mkdtemp(buffer.data()) == nullptr) {
    *error = "cannot create isolated test directory";
    return false;
  }
  out->directory = std::string(buffer.data());
  out->config = config.kdbx;
  out->config.port = port;
  out->config.hdb_dir = out->directory + "/hdb";
  out->instance_id =
      "quant-test-" + std::to_string(getpid()) + "-" +
      std::to_string(
          std::chrono::steady_clock::now().time_since_epoch().count());
  std::filesystem::create_directories(out->config.hdb_dir);

  const std::string log_path = out->directory + "/q.log";
  const int log_fd =
      open(log_path.c_str(), O_WRONLY | O_CREAT | O_APPEND, 0600);
  if (log_fd < 0) {
    *error = "cannot open isolated q log";
    return false;
  }
  posix_spawn_file_actions_t actions;
  posix_spawn_file_actions_init(&actions);
  posix_spawn_file_actions_addopen(&actions, 0, "/dev/null", O_RDONLY, 0);
  posix_spawn_file_actions_adddup2(&actions, log_fd, 1);
  posix_spawn_file_actions_adddup2(&actions, log_fd, 2);
  posix_spawn_file_actions_addclose(&actions, log_fd);

  const std::string qbin = QBinary();
  const std::string endpoint = out->config.host + ":" + std::to_string(port);
  char* argv[] = {const_cast<char*>(qbin.data()),
                  const_cast<char*>(config.kdbx.script.data()),
                  const_cast<char*>("-p"),
                  const_cast<char*>(endpoint.data()),
                  const_cast<char*>("-instanceId"),
                  const_cast<char*>(out->instance_id.data()),
                  nullptr};
  pid_t pid = 0;
  const int spawn_error =
      posix_spawn(&pid, qbin.c_str(), &actions, nullptr, argv, environ);
  posix_spawn_file_actions_destroy(&actions);
  close(log_fd);
  if (spawn_error != 0) {
    *error =
        std::string("isolated q launch failed: ") + std::strerror(spawn_error);
    return false;
  }
  out->pid = pid;
  out->owned = true;

  const std::string command =
      CaptureCommand("ps -p " + std::to_string(pid) + " -o command=");
  if (command.find(config.kdbx.script) == std::string::npos ||
      command.find(endpoint) == std::string::npos ||
      command.find(out->instance_id) == std::string::npos) {
    *error = "isolated q process identity verification failed";
    return false;
  }

  KdbxClient probe(out->config);
  for (int attempt = 0; attempt < 100; ++attempt) {
    if (probe.Connect(error)) break;
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
  }
  if (!probe.connected()) return false;

  std::string handshake;
  if (!probe.QueryJson(".quant.instanceJson", &handshake, error)) return false;
  Json parsed;
  if (!Json::Parse(handshake, &parsed, error)) return false;
  const Json* actual = parsed.Find("instanceId");
  if (actual == nullptr || actual->as_string() != out->instance_id) {
    *error = "isolated q instance id mismatch";
    return false;
  }
  return true;
}

void StopIsolatedQ(IsolatedQ* isolated) {
  if (isolated->pid > 0 && isolated->owned) {
    kill(isolated->pid, SIGTERM);
    int status = 0;
    waitpid(isolated->pid, &status, 0);
  }
  if (!isolated->directory.empty()) {
    std::filesystem::remove_all(isolated->directory);
  }
  isolated->pid = -1;
  isolated->owned = false;
}

int CommandDb(App& app, const std::string& action) {
  const KdbxStatus detected = KdbxClient::Detect();
  if (action == "status") {
    std::cout << "== VERIFIED: KDB-X status ==\n";
    std::cout << "compiled_c_api=" << (detected.compiled ? "true" : "false")
              << " runtime=" << (detected.runtime ? "true" : "false")
              << " license_present="
              << (detected.license_present ? "true" : "false")
              << " pykx=" << (detected.pykx_present ? "true" : "false") << "\n";
    std::cout << "detail: " << detected.detail << "\n";
    std::cout << "process="
              << (QProcessAlive(app.config.kdbx.pid_path) ? "RUNNING"
                                                          : "STOPPED")
              << " endpoint=" << app.config.kdbx.host << ":"
              << app.config.kdbx.port << " (loopback only)\n";
    KdbxClient client(app.config.kdbx);
    std::string error;
    if (client.Connect(&error)) {
      std::string counts;
      if (client.QueryJson(".quant.countsJson", &counts, &error)) {
        std::cout << "connected=true tables=" << counts << "\n";
      } else {
        std::cout << "connected=true query_failed=" << error << "\n";
      }
    } else {
      std::cout << "connected=false: " << error << "\n";
    }
    return 0;
  }
  if (action == "start") {
    if (QProcessAlive(app.config.kdbx.pid_path)) {
      std::cout << "q already running\n";
      return 0;
    }
    const std::string qbin = QBinary();
    const std::string endpoint =
        app.config.kdbx.host + ":" + std::to_string(app.config.kdbx.port);
    std::filesystem::create_directories(
        std::filesystem::path(app.config.kdbx.log_path).parent_path());
    std::filesystem::create_directories(
        std::filesystem::path(app.config.kdbx.pid_path).parent_path());
    std::filesystem::create_directories(app.config.kdbx.hdb_dir);

    const int log_fd = open(app.config.kdbx.log_path.c_str(),
                            O_WRONLY | O_CREAT | O_APPEND, 0644);
    if (log_fd < 0) {
      std::cout << "cannot open q log\n";
      return 1;
    }
    posix_spawn_file_actions_t actions;
    posix_spawn_file_actions_init(&actions);
    posix_spawn_file_actions_addopen(&actions, 0, "/dev/null", O_RDONLY, 0);
    posix_spawn_file_actions_adddup2(&actions, log_fd, 1);
    posix_spawn_file_actions_adddup2(&actions, log_fd, 2);
    posix_spawn_file_actions_addclose(&actions, log_fd);

    std::string program = qbin;
    std::string script = app.config.kdbx.script;
    char* argv[] = {const_cast<char*>(program.data()),
                    const_cast<char*>(script.data()), const_cast<char*>("-p"),
                    const_cast<char*>(endpoint.data()), nullptr};
    pid_t pid = 0;
    const int spawn_error =
        posix_spawn(&pid, program.c_str(), &actions, nullptr, argv, environ);
    posix_spawn_file_actions_destroy(&actions);
    close(log_fd);
    if (spawn_error != 0) {
      std::cout << "failed to launch q: " << std::strerror(spawn_error) << "\n";
      return 1;
    }
    std::ofstream pid_file(app.config.kdbx.pid_path);
    pid_file << pid << "\n";

    KdbxClient client(app.config.kdbx);
    std::string error;
    for (int attempt = 0; attempt < 100; ++attempt) {
      if (client.Connect(&error)) break;
      std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
    std::cout << "q pid=" << pid << " connected="
              << (client.connected() ? "true" : ("false: " + error)) << "\n";
    return client.connected() ? 0 : 1;
  }
  if (action == "stop") {
    std::ifstream input(app.config.kdbx.pid_path);
    long pid = 0;
    if (!input.is_open() || !(input >> pid) || pid <= 0) {
      std::cout << "no valid q pid file\n";
      return 0;
    }
    const std::string command =
        CaptureCommand("ps -p " + std::to_string(pid) + " -o command=");
    const std::string expected_port =
        ":" + std::to_string(app.config.kdbx.port);
    if (command.find(app.config.kdbx.script) == std::string::npos ||
        command.find(expected_port) == std::string::npos) {
      std::cout << "refusing to signal unverified pid=" << pid << "\n";
      return 1;
    }
    if (kill(static_cast<pid_t>(pid), SIGTERM) != 0 && errno != ESRCH) {
      std::cout << "q stop failed\n";
    }
    std::remove(app.config.kdbx.pid_path.c_str());
    std::cout << "q stop requested\n";
    return 0;
  }
  if (action == "verify" || action == "benchmark") {
    std::string error;
    IsolatedQ isolated;
    if (!StartIsolatedQ(app.config, &isolated, &error)) {
      StopIsolatedQ(&isolated);
      std::cout << "FAILED: " << error << "\n";
      return 1;
    }
    const pid_t isolated_pid = isolated.pid;
    const std::string instance_id = isolated.instance_id;
    KdbxClient client(isolated.config);
    std::string report;
    const bool ok = action == "verify" ? client.SelfTest(&report, &error)
                                       : client.Benchmark(&report, &error);
    StopIsolatedQ(&isolated);
    if (!ok) {
      std::cout << "FAILED: " << error << "\n";
      return 1;
    }
    std::cout << (action == "verify"
                      ? "== VERIFIED: isolated C++ to q round-trip ==\n"
                      : "== VERIFIED: isolated KDB-X benchmark ==\n")
              << "isolated_q_pid=" << isolated_pid
              << " instance=" << instance_id << "\n"
              << report;
    return 0;
  }
  std::cout << "usage: quant db status|start|stop|verify|benchmark\n";
  return 1;
}

int CommandDoctor(App& app) {
  std::cout << "== VERIFIED: environment ==\n";
  std::cout << "config: " << kConfigPath << " loaded\n";
  std::cout << "mode: " << ModeName(app.config.mode) << "\n";
  std::cout << "initial capital: INR " << app.config.initial_capital.rupees()
            << "\n";
  std::cout << "journal: " << app.journal.path()
            << " events=" << app.journal.size()
            << " next_sequence=" << app.journal.next_sequence() << "\n";

  std::cout
      << "\n== VERIFIED: keychain service presence (values never read) ==\n";
  for (const CredentialStatus& status : InspectCredentials(app.config)) {
    std::cout << "  " << status.service << ": "
              << (status.present ? "present" : "absent") << " — " << status.note
              << "\n";
  }

  std::cout << "\n== VERIFIED: read-only feed checks ==\n";
  const MarketDataService service(app.config, nullptr);
  for (const FeedStatus& feed : service.CheckFeeds()) {
    std::string provider;
    switch (feed.provider) {
      case Provider::kFyers:
        provider = "FYERS";
        break;
      case Provider::kUpstox:
        provider = "UPSTOX";
        break;
      case Provider::kZerodha:
        provider = "ZERODHA";
        break;
      case Provider::kSynthetic:
        provider = "SYNTHETIC";
        break;
    }
    std::cout << "  " << provider << ": "
              << (feed.available ? "AVAILABLE" : "NOT AVAILABLE")
              << " latency_ms=" << feed.latency_ms << " — " << feed.detail
              << "\n";
  }

  const KdbxStatus kdbx = KdbxClient::Detect();
  std::cout << "\n== VERIFIED: KDB-X ==\n"
            << "  c_api=" << (kdbx.compiled ? "available" : "not compiled")
            << " runtime=" << (kdbx.runtime ? "available" : "absent")
            << " license=" << (kdbx.license_present ? "present" : "absent")
            << " pykx=" << (kdbx.pykx_present ? "available" : "absent") << " — "
            << kdbx.detail << "\n";

  const ZerodhaGateway gateway(app.config, nullptr);
  std::cout << "\n== VERIFIED: execution gate ==\n  "
            << gateway.ComplianceGate() << "\n";
  return 0;
}

int CommandValidate(App& app) {
  const std::vector<std::string> errors = ValidateConfig(app.config);
  std::cout << "config validation: " << (errors.empty() ? "PASS" : "FAIL")
            << "\n";
  for (const std::string& error : errors)
    std::cout << "  ERROR: " << error << "\n";

  const Money notional = Money::FromRupees(100.0);
  const CostBreakdown delivery_buy = EstimateCosts(notional, true, true, 0.0);
  const CostBreakdown intraday_sell =
      EstimateCosts(notional, false, false, 0.0);
  std::cout << "\n== ESTIMATED: cost model spot checks (INR 100 turnover) ==\n";
  std::cout << "  delivery buy: " << FormatCosts(delivery_buy) << "\n";
  std::cout << "  intraday sell: " << FormatCosts(intraday_sell) << "\n";
  std::cout << "  round-trip break-even: "
            << BreakEvenMoveBps(notional, delivery_buy, intraday_sell)
            << " bps\n";
  Portfolio portfolio(app.config.initial_capital);
  std::string error;
  const bool ok = portfolio.Buy(
      "TEST", 1, Money::FromRupees(100.0),
      EstimateCosts(Money::FromRupees(100.0), true, true, 0.0), &error);
  std::cout << "  accounting smoke test: " << (ok ? "PASS" : error) << "\n";
  return errors.empty() ? 0 : 1;
}

int CommandBrokers(App& app) {
  std::cout << "== VERIFIED: broker entitlements ==\n";
  for (const CredentialStatus& status : InspectCredentials(app.config)) {
    std::cout << "  " << status.service << ": "
              << (status.present ? "PRESENT" : "ABSENT") << " — " << status.note
              << "\n";
  }
  const MarketDataService service(app.config, nullptr);
  for (const FeedStatus& feed : service.CheckFeeds()) {
    const char* name = feed.provider == Provider::kFyers ? "FYERS" : "UPSTOX";
    std::cout << "  " << name << " data: "
              << (feed.available ? "WORKING (read-only)" : "NOT AVAILABLE")
              << " — " << feed.detail << "\n";
  }
  const ZerodhaGateway gateway(app.config, nullptr);
  std::string detail;
  std::string error;
  gateway.ReadOnlyConnectivity(&detail, &error);
  std::cout << "  ZERODHA read-only: " << detail << "\n";
  std::cout << "  " << gateway.ComplianceGate() << "\n";
  return 0;
}

int CommandDataSync(App& app) {
  const MarketDataService service(app.config, &app.journal);
  std::cout << "syncing BOD universe from Upstox...\n";
  const UniverseResult result = service.SyncUniverse();
  if (!result.error.empty() && result.instruments.empty()) {
    std::cout << "FAILED: " << result.error << "\n";
    return 1;
  }
  size_t eligible_count = 0;
  for (const Instrument& instrument : result.instruments) {
    if (instrument.eligible) ++eligible_count;
  }
  std::cout << "  fetched=" << result.total_fetched
            << " normalised=" << result.instruments.size()
            << " eligible=" << eligible_count
            << " suspended=" << result.suspended.size() << "\n";
  std::cout << "  source: " << result.source << "\n";
  std::cout << "syncing daily history for seed symbols...\n";
  std::string history_error;
  const bool ok = service.SyncHistory(result.instruments, &history_error);
  std::cout << "  " << (ok ? "done" : "partial") << ": " << history_error
            << "\n";
  return result.instruments.empty() ? 1 : 0;
}

std::string IndexProviderName(const std::string& symbol) {
  if (symbol == "NIFTY 50") return "Nifty 50";
  if (symbol == "NIFTY BANK" || symbol == "BANKNIFTY") return "Nifty Bank";
  if (symbol == "INDIA VIX") return "India VIX";
  if (symbol == "GIFT NIFTY") return "SGX NIFTY";
  return symbol;
}

std::string IndexKey(const std::string& symbol) {
  if (symbol == "NIFTY 50") return "NSE_INDEX|Nifty 50";
  if (symbol == "NIFTY BANK" || symbol == "BANKNIFTY")
    return "NSE_INDEX|Nifty Bank";
  if (symbol == "INDIA VIX") return "NSE_INDEX|India VIX";
  if (symbol == "GIFT NIFTY") return "GLOBAL_INDEX|SGX NIFTY";
  return "";
}

std::vector<std::string> SplitCsv(const std::string& line) {
  std::vector<std::string> fields;
  std::string field;
  bool quoted = false;
  for (size_t i = 0; i < line.size(); ++i) {
    const char c = line[i];
    if (c == '"') {
      if (quoted && i + 1 < line.size() && line[i + 1] == '"') {
        field.push_back('"');
        ++i;
      } else {
        quoted = !quoted;
      }
    } else if (c == ',' && !quoted) {
      fields.push_back(field);
      field.clear();
    } else {
      field.push_back(c);
    }
  }
  fields.push_back(field);
  return fields;
}

std::vector<Instrument> LoadUniverse(App& app, std::string* error) {
  std::ifstream input(app.config.universe_path);
  std::vector<Instrument> instruments;
  if (!input.is_open()) {
    *error = "universe cache missing; run 'quant data sync' first";
    return instruments;
  }
  std::string line;
  std::getline(input, line);
  while (std::getline(input, line)) {
    const std::vector<std::string> fields = SplitCsv(line);
    if (fields.size() < 9) continue;
    Instrument instrument;
    instrument.symbol = fields[0];
    instrument.exchange = fields[1];
    instrument.isin = fields[2];
    instrument.provider_key = fields[3];
    instrument.name = fields[4];
    instrument.security_type = fields[5];
    instrument.tick_size = std::stod(fields[6]);
    instrument.cas_eligible = fields[7] == "true";
    instrument.eligible = fields[8] == "true";
    if (fields.size() > 10) {
      instrument.instrument_class =
          static_cast<InstrumentClass>(std::stoi(fields[10]));
    } else {
      instrument.instrument_class = InstrumentClass::kEquity;
    }
    instruments.push_back(instrument);
  }
  return instruments;
}

std::map<std::string, std::vector<Candle>> LoadHistory(App& app) {
  std::map<std::string, std::vector<Candle>> history;
  std::ifstream directory_list;
  for (const std::string& symbol : app.config.seed_symbols) {
    std::string error;
    std::vector<Candle> candles;
    if (ReadCsvCandles(app.config.history_dir + "/" + symbol + ".csv", &candles,
                       &error)) {
      history[symbol] = candles;
    }
  }
  for (const std::string& symbol : app.config.index_symbols) {
    std::string error;
    std::vector<Candle> candles;
    if (ReadCsvCandles(app.config.history_dir + "/" + symbol + ".csv", &candles,
                       &error)) {
      history[symbol] = candles;
    }
  }
  (void)directory_list;
  return history;
}

int CommandUniverse(App& app) {
  std::string error;
  const std::vector<Instrument> instruments = LoadUniverse(app, &error);
  if (!error.empty()) {
    std::cout << "FAILED: " << error << "\n";
    return 1;
  }
  size_t eligible = 0;
  std::vector<std::string> names;
  for (const Instrument& instrument : instruments) {
    if (!instrument.eligible) continue;
    ++eligible;
    if (names.size() < 25) names.push_back(instrument.symbol);
  }
  std::cout << "== VERIFIED: dynamic NSE universe from Upstox BOD file ==\n";
  std::cout << "total=" << instruments.size() << " eligible=" << eligible
            << " source=" << app.config.universe_path << "\n";
  std::cout << "sample: ";
  for (size_t i = 0; i < names.size(); ++i) {
    if (i > 0) std::cout << ", ";
    std::cout << names[i];
  }
  std::cout << "\n";
  return 0;
}

DailyReport BuildReport(App& app, bool* ok) {
  DailyReport report;
  report.generated_at_utc = NowUtc();
  *ok = false;
  const MarketDataService service(app.config, &app.journal);
  service.MarketStatus(&report.market_status, nullptr);

  std::string universe_error;
  const std::vector<Instrument> universe = LoadUniverse(app, &universe_error);
  if (!universe_error.empty()) {
    report.decision_reason = universe_error;
    report.data_provenance = "none";
    return report;
  }
  std::set<std::string> seed(app.config.seed_symbols.begin(),
                             app.config.seed_symbols.end());
  std::vector<Instrument> candidates;
  for (const Instrument& instrument : universe) {
    if (instrument.eligible && seed.count(instrument.symbol) > 0) {
      candidates.push_back(instrument);
    }
  }
  for (const std::string& symbol : app.config.index_symbols) {
    const std::string key = IndexKey(symbol);
    if (key.empty()) continue;
    Instrument index;
    index.symbol = symbol;
    index.provider_key = key;
    index.exchange = "INDEX";
    index.instrument_class = InstrumentClass::kIndex;
    candidates.push_back(index);
  }

  std::vector<Quote> quotes;
  std::string quote_error;
  const bool quotes_ok = service.FetchQuotes(candidates, &quotes, &quote_error);
  if (!quotes_ok) {
    report.decision_reason = "live quotes unavailable: " + quote_error;
    report.data_provenance = "Upstox BOD universe (cached); quotes unavailable";
    return report;
  }
  report.data_provenance =
      "Upstox analytics token (read-only) quotes + BOD instrument file; "
      "daily candles cached from Upstox historical API; FYERS token unusable";

  std::map<std::string, Quote> by_symbol;
  for (const Quote& quote : quotes) by_symbol[quote.symbol] = quote;
  for (const std::string& line : report.index_summary) (void)line;
  report.index_summary.clear();
  std::vector<Quote> global_quotes;
  std::string global_error;
  if (service.GlobalIndices(&global_quotes, &global_error)) {
    for (const Quote& quote : global_quotes) by_symbol[quote.symbol] = quote;
  }
  for (const std::string& symbol : app.config.index_symbols) {
    auto it = by_symbol.find(IndexProviderName(symbol));
    if (it == by_symbol.end()) {
      it = by_symbol.find(symbol);
    }
    if (it == by_symbol.end()) continue;
    std::ostringstream oss;
    oss << it->first << ": last=" << std::fixed << std::setprecision(2)
        << it->second.last
        << " source=VERIFIED timestamp_ms=" << it->second.exchange_timestamp_ms;
    report.index_summary.push_back(oss.str());
  }

  const auto history = LoadHistory(app);
  auto index_it = history.find("NIFTY 50");
  const std::vector<Candle> no_candles;
  report.regime =
      index_it == history.end() ? 0.0 : RegimeScore(index_it->second);

  Portfolio paper(app.config.initial_capital);
  RiskEngine risk(app.config);
  const int64_t now = std::chrono::duration_cast<std::chrono::milliseconds>(
                          std::chrono::system_clock::now().time_since_epoch())
                          .count();
  for (const Instrument& instrument : candidates) {
    auto quote_it = by_symbol.find(instrument.symbol);
    if (quote_it == by_symbol.end() ||
        instrument.instrument_class != InstrumentClass::kEquity) {
      continue;
    }
    const Quote& quote = quote_it->second;
    const double traded_value = quote.last * quote.volume;
    if (traded_value < app.config.min_traded_value_inr) {
      report.rejections.push_back(instrument.symbol +
                                  ": insufficient traded value (INR " +
                                  std::to_string(traded_value) + ")");
      continue;
    }
    auto history_it = history.find(instrument.symbol);
    if (history_it == history.end() || history_it->second.size() < 130) {
      report.rejections.push_back(
          instrument.symbol + ": insufficient daily history (INCONCLUSIVE)");
      continue;
    }
    const FeatureSet features = ComputeFeatures(history_it->second);
    const double score = EnsembleScore(features, report.regime);
    if (score <= 0.35) {
      report.rejections.push_back(
          instrument.symbol +
          ": ensemble score below 0.35 (score=" + std::to_string(score) + ")");
      continue;
    }
    if (report.regime < -0.2) {
      report.rejections.push_back(instrument.symbol +
                                  ": adverse market regime");
      continue;
    }
    const double spread = SpreadBps(quote);
    if (!std::isfinite(spread) || spread > app.config.max_spread_bps) {
      report.rejections.push_back(
          instrument.symbol + ": spread missing or above limit (estimated " +
          (std::isfinite(spread) ? std::to_string(spread) : "inf") + " bps)");
      continue;
    }
    Signal signal;
    signal.symbol = instrument.symbol;
    signal.strategy = "regime_aware_ensemble";
    signal.score = score;
    signal.features = features;
    signal.tradable = true;
    const RiskDecision decision = risk.Evaluate(signal, quote, paper, now);
    if (!decision.approved) {
      report.rejections.push_back(instrument.symbol + ": risk rejected — " +
                                  decision.reason);
      continue;
    }
    CandidateReport candidate;
    candidate.symbol = instrument.symbol;
    candidate.instrument_key = instrument.provider_key;
    candidate.last = quote.last;
    candidate.spread_bps = spread;
    candidate.score = score;
    candidate.strategy = signal.strategy;
    candidate.quantity = decision.quantity;
    candidate.notional = decision.notional;
    const CostBreakdown entry =
        EstimateCosts(decision.notional, app.config.backtest_delivery, true,
                      app.config.slippage_bps);
    const CostBreakdown exit_costs =
        EstimateCosts(decision.notional, app.config.backtest_delivery, false,
                      app.config.slippage_bps);
    candidate.total_cost = entry.total + exit_costs.total;
    candidate.break_even_bps =
        BreakEvenMoveBps(decision.notional, entry, exit_costs);
    candidate.entry =
        "only above observed opening-range high with relative volume "
        "confirmation and verified live depth; never at market open";
    candidate.invalidation = "price below prior-day low or 2x ATR adverse move";
    candidate.exit = "3-session horizon, VWAP loss or 5% stop, whichever first";
    candidate.rationale =
        "momentum=" + std::to_string(features.ret20) +
        " trend=" + std::to_string(features.trend) +
        " volume_surprise=" + std::to_string(features.volume_surprise) +
        " (all ESTIMATED from cached daily candles; no intraday order book "
        "available)";
    report.candidates.push_back(candidate);
  }
  std::sort(report.candidates.begin(), report.candidates.end(),
            [](const CandidateReport& a, const CandidateReport& b) {
              return a.score > b.score;
            });
  if (report.candidates.size() > app.config.shortlist_size) {
    report.candidates.resize(app.config.shortlist_size);
  }
  if (report.candidates.empty()) {
    report.decision = "NO TRADE";
    report.decision_reason =
        report.market_status.find("OPEN") == std::string::npos
            ? "no candidate passed filters and market is not in a verified "
              "open session"
            : "no candidate passed filters with live depth and risk approval";
  } else {
    report.decision = "SIMULATED SHORTLIST ONLY";
    report.decision_reason =
        "conditional research candidates; live execution remains locked and "
        "order-book confirmation is required before paper entry";
  }
  *ok = true;
  return report;
}

int CommandMarketReport(App& app) {
  bool ok = false;
  const DailyReport report = BuildReport(app, &ok);
  const std::string text = RenderReport(report);
  const std::string path = app.config.report_dir + "/daily-" +
                           report.generated_at_utc.substr(0, 10) + ".md";
  const std::string error = WriteTextFile(path, text);
  if (!error.empty()) {
    std::cout << "report write failed: " << error << "\n";
  }
  std::cout << text;
  std::cout << "\nSaved: " << (error.empty() ? path : "not saved") << "\n";
  return ok ? 0 : 2;
}

int CommandResearch(App& app) {
  const auto history = LoadHistory(app);
  if (history.size() < 10) {
    std::cout << "INCONCLUSIVE: fewer than 10 cached series; run 'quant data "
                 "sync'.\n";
    return 2;
  }
  const BacktestMetrics metrics = RunBacktest(history, app.config);
  std::ostringstream oss;
  oss << "== SIMULATED: deterministic event-driven backtest ==\n"
      << "series=" << history.size() << " trades=" << metrics.trades
      << " wins=" << metrics.wins << "\n"
      << "gross_pnl_inr=" << std::fixed << std::setprecision(2)
      << metrics.gross_pnl.rupees() << "\n"
      << "net_pnl_inr=" << metrics.net_pnl.rupees() << "\n"
      << "realized_inr=" << metrics.realized_pnl.rupees()
      << " unrealized_inr=" << metrics.unrealized_pnl.rupees() << "\n"
      << "brokerage_inr=" << metrics.brokerage.rupees()
      << " statutory_inr=" << metrics.statutory.rupees()
      << " open_positions=" << metrics.open_positions << "\n"
      << "max_drawdown_inr=" << metrics.max_drawdown.rupees() << "\n"
      << "final_equity_inr=" << metrics.final_equity << "\n"
      << "sharpe=" << metrics.sharpe << " sortino=" << metrics.sortino << "\n"
      << "profit_factor_estimate=" << metrics.profit_factor << "\n"
      << "cost_to_gross=" << metrics.cost_to_gross << "\n"
      << "labels: SIMULATED fills at next-day open, conservative 10 bps "
         "slippage, "
         "no order-book history, no survivorship-bias correction.\n";
  const std::string json =
      "{\"trades\":" + std::to_string(metrics.trades) +
      ",\"net_pnl_inr\":" + std::to_string(metrics.net_pnl.rupees()) +
      ",\"sharpe\":" + std::to_string(metrics.sharpe) +
      ",\"max_drawdown_inr\":" + std::to_string(metrics.max_drawdown.rupees()) +
      "}";
  WriteTextFile(app.config.report_dir + "/backtest.json", json);
  WriteTextFile(app.config.report_dir + "/backtest.md", oss.str());
  std::cout << oss.str();
  std::cout << "Saved: " << app.config.report_dir << "/backtest.json\n";
  return 0;
}

int CommandVerify(App& app) {
  const auto history = LoadHistory(app);
  auto it = history.find("NIFTY 50");
  if (it == history.end() || it->second.size() < 60) {
    std::cout << "INCONCLUSIVE: index history insufficient for verification.\n";
    return 2;
  }
  std::vector<double> returns;
  for (size_t i = 1; i < it->second.size(); ++i) {
    const double previous = it->second[i - 1].close;
    if (previous > 0.0) returns.push_back(it->second[i].close / previous - 1.0);
  }
  const auto ci = BootstrapSharpeCI(returns, 20261125, 2000);
  std::ostringstream oss;
  oss << std::fixed << std::setprecision(4);
  oss << "== ESTIMATED: statistical verification on NIFTY 50 baseline ==\n"
      << "observations=" << returns.size() << " trials=8 (strategy families)\n"
      << "bootstrap_sharpe_95ci=[" << ci[0] << ", " << ci[2]
      << "] median=" << ci[1] << "\n"
      << "deflated_sharpe_8_trials=" << DeflatedSharpe(ci[1], 8, returns.size())
      << "\n"
      << "INCONCLUSIVE: no strategy result is validated by this baseline "
         "check; "
         "purged walk-forward, embargo, bootstrap CI, deflated IC and a "
         "CSCV-style "
         "PBO proxy are in python/research.py; White Reality Check, Hansen SPA "
         "and "
         "full CSCV logit distribution are NOT IMPLEMENTED.\n";
  WriteTextFile(app.config.report_dir + "/verification.md", oss.str());
  std::cout << oss.str();
  return 0;
}

int CommandReplay(App& app) {
  std::string error;
  const std::vector<Event> events = app.journal.Replay(&error);
  std::cout << "== VERIFIED: journal replay ==\n";
  if (!error.empty()) {
    std::cout << "FAILED: " << error << "\n";
    return 1;
  }
  std::cout << "events=" << events.size() << " integrity=PASS checksums=PASS\n";
  for (size_t i = 0; i < std::min<size_t>(events.size(), 10); ++i) {
    std::cout << "  seq=" << events[i].sequence
              << " type=" << static_cast<int>(events[i].type)
              << " id=" << events[i].id << " payload=" << events[i].payload
              << "\n";
  }
  return 0;
}

bool RebuildPortfolio(App& app, Portfolio* out, std::string* error) {
  Portfolio portfolio(app.config.initial_capital);
  const std::vector<Event> events = app.journal.Replay(error);
  if (!error->empty()) return false;
  for (const Event& event : events) {
    if (event.type != EventType::kAccounting) continue;
    Json payload;
    if (!Json::Parse(event.payload, &payload, error)) return false;
    const Json* action = payload.Find("action");
    const Json* symbol = payload.Find("symbol");
    const Json* qty = payload.Find("qty");
    const Json* price = payload.Find("price_paise");
    if (action == nullptr || symbol == nullptr || qty == nullptr ||
        price == nullptr) {
      *error = "accounting event " + std::to_string(event.sequence) +
               " is missing required fields";
      return false;
    }
    const Money value = Money::FromPaise(price->as_int(0));
    const int quantity = static_cast<int>(qty->as_int(0));
    bool posted = false;
    if (action->as_string() == "BUY") {
      const CostBreakdown costs =
          EstimateCosts(Money::FromPaise(value.paise() * quantity), false, true,
                        app.config.slippage_bps);
      posted =
          portfolio.Buy(symbol->as_string(), quantity, value, costs, error);
    } else if (action->as_string() == "SELL") {
      const CostBreakdown costs =
          EstimateCosts(Money::FromPaise(value.paise() * quantity), false,
                        false, app.config.slippage_bps);
      posted =
          portfolio.Sell(symbol->as_string(), quantity, value, costs, error);
    } else if (action->as_string() == "EXPENSE") {
      posted = portfolio.RecordOperatingExpense(value, error);
    } else if (action->as_string() == "FLOW") {
      portfolio.RecordExternalFlow(value, "external flow observed");
      posted = true;
    } else {
      *error = "unknown accounting action at sequence " +
               std::to_string(event.sequence);
      return false;
    }
    if (!posted) {
      *error = "accounting replay failed at sequence " +
               std::to_string(event.sequence) + ": " + *error;
      return false;
    }
  }
  *out = portfolio;
  error->clear();
  return true;
}

int CommandPaper(App& app) {
  bool ok = false;
  const DailyReport report = BuildReport(app, &ok);
  if (!ok) {
    std::cout << "PAPER: no action — " << report.decision_reason << "\n";
    return 2;
  }
  Portfolio portfolio;
  std::string replay_error;
  if (!RebuildPortfolio(app, &portfolio, &replay_error)) {
    std::cout << "FAILED: portfolio replay — " << replay_error << "\n";
    return 1;
  }
  std::cout << "== SIMULATED: paper pass, no broker orders ==\n";
  std::cout << "market_status=" << report.market_status << "\n";
  if (report.candidates.empty()) {
    std::cout << "NO TRADE — " << report.decision_reason << "\n";
  } else {
    const CandidateReport& candidate = report.candidates.front();
    std::cout << "would_enter=" << candidate.symbol
              << " qty=" << candidate.quantity
              << " estimated_cost_inr=" << candidate.total_cost.rupees()
              << "\n";
    std::cout
        << "entry is conditional on live opening-range confirmation; "
           "no simulated fill recorded without a verified open session.\n";
    if (report.market_status.find("OPEN") != std::string::npos) {
      Json::Object payload;
      payload["action"] = Json::String("PAPER_ENTRY_CONDITIONAL");
      payload["symbol"] = Json::String(candidate.symbol);
      payload["qty"] = Json::Number(candidate.quantity);
      payload["price_paise"] = Json::Number(
          static_cast<double>(Money::FromRupees(candidate.last).paise()));
      Event event;
      event.type = EventType::kPaperIntent;
      event.payload = Json::ObjectValue(payload).Dump();
      std::string error;
      if (!app.journal.Append(&event, &error)) {
        std::cout << "FAILED: paper intent journal append — " << error << "\n";
        return 1;
      }
      std::cout << "journal: conditional paper intent recorded\n";
    }
  }
  const Ledger ledger = portfolio.ledger();
  std::cout << "equity_inr=" << portfolio.equity().rupees()
            << " cash_inr=" << ledger.cash.rupees()
            << " drawdown_inr=" << portfolio.drawdown().rupees() << "\n";
  return 0;
}

int CommandPortfolio(App& app) {
  Portfolio portfolio;
  std::string replay_error;
  if (!RebuildPortfolio(app, &portfolio, &replay_error)) {
    std::cout << "FAILED: portfolio replay — " << replay_error << "\n";
    return 1;
  }
  const Ledger ledger = portfolio.ledger();
  std::cout << "== VERIFIED: event-sourced ledger (SIMULATED unless paper "
               "fills) ==\n";
  std::cout << "initial_capital_inr=" << ledger.initial_capital.rupees() << "\n"
            << "cash_inr=" << ledger.cash.rupees() << "\n"
            << "invested_inr=" << ledger.invested.rupees() << "\n"
            << "realized_pnl_inr=" << ledger.realized_pnl.rupees() << "\n"
            << "unrealized_pnl_inr=" << ledger.unrealized_pnl.rupees() << "\n"
            << "brokerage_inr=" << ledger.brokerage.rupees() << "\n"
            << "statutory_inr=" << ledger.statutory.rupees() << "\n"
            << "operating_inr=" << ledger.operating.rupees() << "\n"
            << "estimated_tax_inr=" << ledger.estimated_tax.rupees() << "\n"
            << "equity_inr=" << portfolio.equity().rupees() << "\n"
            << "peak_equity_inr=" << ledger.peak_equity.rupees() << "\n"
            << "drawdown_inr=" << portfolio.drawdown().rupees() << "\n"
            << "reinvestable_realized_profit_inr="
            << std::max(0.0, ledger.realized_pnl.rupees()) << "\n"
            << "external_flows_inr=" << ledger.external_flows.rupees()
            << " (must be zero; non-zero is a reconciliation exception)\n"
            << "open_positions=" << portfolio.open_position_count() << "\n";
  return 0;
}

int CommandReport(App& app) {
  const std::string path =
      app.config.report_dir + "/daily-" + NowUtc().substr(0, 10) + ".md";
  std::ifstream input(path);
  if (!input.is_open()) {
    std::cout << "no report for today; run 'quant market report'.\n";
    return 1;
  }
  std::cout << input.rdbuf();
  return 0;
}

}  // namespace

int RunCli(int argc, char** argv) {
  if (argc < 2) {
    PrintHelp();
    return 0;
  }
  const std::string command = argv[1];
  if (command == "help" || command == "--help" || command == "-h") {
    PrintHelp();
    return 0;
  }
  std::string error;
  App app = Initialize(&error);
  if (!app.ready) {
    std::cout << "INITIALISATION FAILED: " << error << "\n";
    return 1;
  }
  if (command == "doctor") return CommandDoctor(app);
  if (command == "validate") return CommandValidate(app);
  if (command == "db" && argc >= 3) return CommandDb(app, argv[2]);
  if (command == "brokers" && argc >= 3 && std::string(argv[2]) == "status") {
    return CommandBrokers(app);
  }
  if (command == "data" && argc >= 3 && std::string(argv[2]) == "sync") {
    return CommandDataSync(app);
  }
  if (command == "universe" && argc >= 3 && std::string(argv[2]) == "scan") {
    return CommandUniverse(app);
  }
  if (command == "market" && argc >= 3 && std::string(argv[2]) == "report") {
    return CommandMarketReport(app);
  }
  if (command == "research" && argc >= 3 && std::string(argv[2]) == "verify") {
    return CommandVerify(app);
  }
  if (command == "research") return CommandResearch(app);
  if (command == "backtest") return CommandResearch(app);
  if (command == "verify") return CommandVerify(app);
  if (command == "replay") return CommandReplay(app);
  if (command == "paper" && argc >= 3 && std::string(argv[2]) == "start") {
    return CommandPaper(app);
  }
  if (command == "portfolio") return CommandPortfolio(app);
  if (command == "report") return CommandReport(app);
  PrintHelp();
  return 1;
}

}  // namespace quant

int main(int argc, char** argv) { return quant::RunCli(argc, argv); }
