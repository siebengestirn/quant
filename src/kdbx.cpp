#include "quant.hpp"

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <iomanip>
#include <numeric>
#include <set>
#include <sstream>

#ifdef QUANT_HAS_KDBX
#include <k.h>
#endif

namespace quant {
namespace {
const char* ProviderName(Provider provider) {
  switch (provider) {
    case Provider::kFyers:
      return "FYERS";
    case Provider::kUpstox:
      return "UPSTOX";
    case Provider::kZerodha:
      return "ZERODHA";
    case Provider::kSynthetic:
      return "SYNTHETIC";
  }
  return "UNKNOWN";
}

#ifdef QUANT_HAS_KDBX

const int64_t kKxEpochOffsetMs = 946684800000LL;  // 2000-01-01 UTC.

int64_t UnixMsToKxNs(int64_t ms) { return (ms - kKxEpochOffsetMs) * 1000000LL; }

int DateToKxDays(const std::string& date) {
  std::tm tm{};
  std::istringstream ss(date);
  ss >> std::get_time(&tm, "%Y-%m-%d");
  if (ss.fail()) return 0;
  return static_cast<int>((timegm(&tm) - 946684800) / 86400);
}

#endif  // QUANT_HAS_KDBX

bool FileExists(const std::string& path) {
  return std::fopen(path.c_str(), "r") != nullptr;
}

std::string RunCapture(const std::string& command) {
  std::array<char, 1024> buffer{};
  std::string output;
  FILE* pipe = popen(command.c_str(), "r");
  if (pipe == nullptr) return "";
  while (fgets(buffer.data(), static_cast<int>(buffer.size()), pipe) !=
         nullptr) {
    output += buffer.data();
  }
  pclose(pipe);
  while (!output.empty() && (output.back() == '\n' || output.back() == '\r')) {
    output.pop_back();
  }
  return output;
}

double Percentile(std::vector<double> values, double fraction) {
  if (values.empty()) return 0.0;
  std::sort(values.begin(), values.end());
  const size_t index = std::min(
      values.size() - 1,
      static_cast<size_t>(fraction * static_cast<double>(values.size())));
  return values[index];
}

}  // namespace

struct KdbxClient::Impl {
  KdbxConfig config;
  int handle = -1;
  bool connected = false;

  explicit Impl(KdbxConfig cfg) : config(std::move(cfg)) {}

#ifdef QUANT_HAS_KDBX
  K Call(const std::string& function, K argument) {
    if (!connected) return static_cast<K>(0);
    // Over IPC a bare function name returns the function value; "[]" invokes
    // it.
    const std::string expression =
        argument == static_cast<K>(0) ? function + "[]" : function;
    K result = argument == static_cast<K>(0)
                   ? k(handle, expression.c_str(), static_cast<K>(0))
                   : k(handle, expression.c_str(), argument, static_cast<K>(0));
    return result;
  }

  bool CallOk(const std::string& function, K argument, std::string* error) {
    K result = Call(function, argument);
    if (result == static_cast<K>(0)) {
      if (error) *error = "no IPC response from q for " + function;
      return false;
    }
    if (result->t == 104 || result->t == -128) {
      if (error) *error = "q error in " + function;
      r0(result);
      return false;
    }
    r0(result);
    return true;
  }

  bool CallJson(const std::string& function, K argument, std::string* out,
                std::string* error) {
    K result = Call(function, argument);
    if (result == static_cast<K>(0)) {
      if (error) *error = "no IPC response from q for " + function;
      return false;
    }
    // IPC error responses carry type 104 in the KX protocol.
    if (result->t == 104 || result->t == -128) {
      if (error) *error = "q error in " + function;
      r0(result);
      return false;
    }
    const int atom_type = result->t < 0 ? -result->t : result->t;
    if (atom_type == KS && result->n == 1) {
      *out = std::string(kS(result)[0]);
    } else if (atom_type == KC && result->n > 0) {
      *out = std::string(kC(result), kC(result) + result->n);
    } else {
      if (error) {
        *error = "expected a JSON string from " + function + " (type " +
                 std::to_string(result->t) + ")";
      }
      r0(result);
      return false;
    }
    r0(result);
    return true;
  }
#endif
};

KdbxClient::KdbxClient(KdbxConfig config)
    : impl_(std::make_unique<Impl>(std::move(config))) {}

KdbxClient::~KdbxClient() { Disconnect(); }

KdbxStatus KdbxClient::Detect() {
  KdbxStatus status;
#ifdef QUANT_HAS_KDBX
  status.compiled = true;
#else
  status.detail =
      "C API not compiled in (QUANT_WITH_KDBX=OFF or k.h not found)";
#endif
  const char* qhome = std::getenv("QHOME");
  const std::string qbinary =
      (qhome != nullptr ? std::string(qhome) + "/bin/q" : std::string("q"));
  status.runtime = !RunCapture("test -x " + qbinary + " && echo yes").empty();
  const std::string license =
      (qhome != nullptr ? std::string(qhome) + "/kc.lic" : std::string());
  status.license_present = !license.empty() && FileExists(license);
#ifdef QUANT_PYKX_DIR
  // Filesystem check avoids a ~20 s PyKX import on every doctor/validate run.
  status.pykx_present =
      FileExists(std::string(QUANT_PYKX_DIR) + "/__init__.py");
#else
  const char* python = std::getenv("QUANT_PYTHON_EXECUTABLE");
  const std::string python_cmd =
      python != nullptr ? std::string(python) : std::string("python3");
  status.pykx_present =
      !RunCapture(python_cmd + " -c 'import pykx' 2>/dev/null && echo yes")
           .empty();
#endif
  if (status.compiled && status.runtime && status.license_present) {
    status.detail = "KDB-X runtime, license and KX C API detected";
  } else if (status.compiled && status.runtime && !status.license_present) {
    status.detail = "KDB-X runtime detected; license file not found";
  }
  return status;
}

bool KdbxClient::Connect(std::string* error) {
  if (!impl_->config.enabled) {
    if (error) *error = "KDB-X disabled in configuration";
    return false;
  }
#ifdef QUANT_HAS_KDBX
  if (impl_->connected) return true;
  impl_->handle =
      khpun(impl_->config.host.c_str(), impl_->config.port,
            impl_->config.username.c_str(), impl_->config.timeout_ms);
  if (impl_->handle < 0) {
    if (error) {
      *error = impl_->handle == -2 ? "KDB-X connection timeout"
                                   : "KDB-X connection failed (code " +
                                         std::to_string(impl_->handle) + ")";
    }
    return false;
  }
  impl_->connected = true;
  return true;
#else
  if (error) *error = "KDB-X C API unavailable in this build";
  return false;
#endif
}

void KdbxClient::Disconnect() {
#ifdef QUANT_HAS_KDBX
  if (impl_->connected) kclose(impl_->handle);
#endif
  impl_->connected = false;
}

bool KdbxClient::connected() const { return impl_->connected; }

bool KdbxClient::Reset(std::string* error) {
#ifdef QUANT_HAS_KDBX
  if (!Connect(error)) return false;
  return impl_->CallOk(".quant.reset", static_cast<K>(0), error);
#else
  if (error) *error = "KDB-X C API unavailable";
  return false;
#endif
}

bool KdbxClient::IngestQuote(const Quote& quote, uint64_t sequence,
                             const std::string& event_id, std::string* error) {
#ifdef QUANT_HAS_KDBX
  if (!Connect(error)) return false;
  // Typed KX IPC: 64-bit sequences and nanosecond timestamps remain integers.
  // The JSON path is retained only for diagnostics and compatibility.
  K args = knk(
      13, kj(static_cast<long long>(sequence)),
      ks(const_cast<char*>(event_id.c_str())),
      ks(const_cast<char*>(quote.symbol.c_str())),
      ks(const_cast<char*>(ProviderName(quote.source))),
      kj(static_cast<long long>(UnixMsToKxNs(quote.exchange_timestamp_ms))),
      kj(static_cast<long long>(UnixMsToKxNs(quote.provider_timestamp_ms))),
      kj(static_cast<long long>(UnixMsToKxNs(
          quote.last_trade_timestamp_ms > 0 ? quote.last_trade_timestamp_ms
                                            : quote.exchange_timestamp_ms))),
      kj(static_cast<long long>(quote.receive_timestamp_ms) * 1000000LL),
      kf(quote.last), kf(quote.bid), kf(quote.ask), kf(quote.volume),
      ks(const_cast<char*>(quote.quality.empty() ? "ok"
                                                 : quote.quality.c_str())));
  return impl_->CallOk(".quant.ingest", args, error);
#else
  (void)quote;
  (void)sequence;
  (void)event_id;
  if (error) *error = "KDB-X C API unavailable";
  return false;
#endif
}

bool KdbxClient::IngestBar(const Candle& candle, const std::string& symbol,
                           std::string* error) {
#ifdef QUANT_HAS_KDBX
  if (!Connect(error)) return false;
  K args =
      knk(7, kj(static_cast<long long>(DateToKxDays(candle.date))),
          ks(const_cast<char*>(symbol.c_str())), kf(candle.open),
          kf(candle.high), kf(candle.low), kf(candle.close), kf(candle.volume));
  return impl_->CallOk(".quant.ingestBar", args, error);
#else
  (void)candle;
  (void)symbol;
  if (error) *error = "KDB-X C API unavailable";
  return false;
#endif
}

bool KdbxClient::QueryJson(const std::string& function, std::string* out,
                           std::string* error) {
  static const std::set<std::string> kAllowed = {
      ".quant.countsJson",   ".quant.stateJson",      ".quant.featuresJson",
      ".quant.dbStatusJson", ".quant.historicalJson", ".quant.instanceJson"};
  if (kAllowed.count(function) == 0) {
    if (error) *error = "q function not whitelisted: " + function;
    return false;
  }
#ifdef QUANT_HAS_KDBX
  if (!Connect(error)) return false;
  K argument = static_cast<K>(0);
  if (function == ".quant.historicalJson" || function == ".quant.recoverJson") {
    argument = ks(const_cast<char*>(impl_->config.hdb_dir.c_str()));
  }
  return impl_->CallJson(function, argument, out, error);
#else
  (void)out;
  if (error) *error = "KDB-X C API unavailable";
  return false;
#endif
}

bool KdbxClient::Persist(std::string* error) {
#ifdef QUANT_HAS_KDBX
  if (!Connect(error)) return false;
  K path = ks(const_cast<char*>(impl_->config.hdb_dir.c_str()));
  return impl_->CallOk(".quant.persist", path, error);
#else
  if (error) *error = "KDB-X C API unavailable";
  return false;
#endif
}

bool KdbxClient::LoadHistorical(std::string* out, std::string* error) {
  return QueryJson(".quant.historicalJson", out, error);
}

bool KdbxClient::SelfTest(std::string* report, std::string* error) {
  std::ostringstream oss;
  if (!Reset(error)) return false;
  const int64_t now = std::chrono::duration_cast<std::chrono::milliseconds>(
                          std::chrono::system_clock::now().time_since_epoch())
                          .count();
  auto make_quote = [now](int64_t seq, double price) {
    Quote quote;
    quote.symbol = "TEST";
    quote.source = Provider::kUpstox;
    quote.last = price;
    quote.bid = price - 0.05;
    quote.ask = price + 0.05;
    quote.volume = 1000.0;
    quote.exchange_timestamp_ms = now - 60000 + seq;
    quote.provider_timestamp_ms = quote.exchange_timestamp_ms;
    quote.receive_timestamp_ms = now;
    quote.quality = "ok";
    return quote;
  };
  if (!IngestQuote(make_quote(1, 100.0), 1, "evt-1", error)) return false;
  if (!IngestQuote(make_quote(2, 101.0), 2, "evt-2", error)) return false;
  if (!IngestQuote(make_quote(0, 99.0), 0, "evt-0", error))
    return false;  // out of order.
  if (!IngestQuote(make_quote(2, 101.0), 2, "evt-2", error))
    return false;  // duplicate.

  std::string counts;
  std::string state;
  if (!QueryJson(".quant.countsJson", &counts, error) ||
      !QueryJson(".quant.stateJson", &state, error)) {
    return false;
  }
  Json counts_json;
  Json state_json;
  if (!Json::Parse(counts, &counts_json, error) ||
      !Json::Parse(state, &state_json, error)) {
    return false;
  }
  const Json* quotes = counts_json.Find("quotes");
  const Json* duplicates = state_json.Find("duplicates");
  const Json* out_of_order = state_json.Find("outOfOrder");
  if (quotes == nullptr || duplicates == nullptr || out_of_order == nullptr ||
      quotes->as_int(0) != 3 || duplicates->as_int(0) != 1 ||
      out_of_order->as_int(0) != 1) {
    if (error) *error = "KDB-X duplicate/out-of-order invariants failed";
    return false;
  }
  oss << "counts=" << counts << "\nstate=" << state << "\n";

  Candle bar;
  bar.date = "2026-11-20";
  bar.open = 100.0;
  bar.high = 101.0;
  bar.low = 99.5;
  bar.close = 100.5;
  bar.volume = 1000000.0;
  if (!IngestBar(bar, "TEST", error)) return false;
  std::string features;
  if (!QueryJson(".quant.featuresJson", &features, error)) return false;
  oss << "features=" << features << "\n";
  if (!Persist(error)) return false;
  std::string historical;
  if (!LoadHistorical(&historical, error)) return false;
  Json historic_json;
  if (!Json::Parse(historical, &historic_json, error)) return false;
  if (historic_json.Find("bars") == nullptr ||
      historic_json.Find("meta") == nullptr) {
    if (error) *error = "historical partition missing tables after persist";
    return false;
  }
  oss << "historical_tables=" << historical.substr(0, 160) << "\n";
  *report = oss.str();
  return true;
}

bool KdbxClient::Benchmark(std::string* report, std::string* error) {
  std::ostringstream oss;
  if (!Reset(error)) return false;
  const size_t count = 1000;
  const int64_t now = std::chrono::duration_cast<std::chrono::milliseconds>(
                          std::chrono::system_clock::now().time_since_epoch())
                          .count();
  std::vector<double> latencies_ms;
  latencies_ms.reserve(count);
  for (size_t i = 0; i < count; ++i) {
    Quote quote;
    quote.symbol = "BENCH";
    quote.last = 100.0 + static_cast<double>(i % 100) * 0.01;
    quote.bid = quote.last - 0.05;
    quote.ask = quote.last + 0.05;
    quote.volume = 1000.0;
    quote.exchange_timestamp_ms = now - 60000 + static_cast<int64_t>(i);
    quote.provider_timestamp_ms = quote.exchange_timestamp_ms;
    quote.receive_timestamp_ms = now;
    quote.quality = "ok";
    const auto started = std::chrono::steady_clock::now();
    if (!IngestQuote(quote, static_cast<uint64_t>(i + 1),
                     "bench-" + std::to_string(i), error)) {
      return false;
    }
    latencies_ms.push_back(std::chrono::duration<double, std::milli>(
                               std::chrono::steady_clock::now() - started)
                               .count());
  }
  const auto query_started = std::chrono::steady_clock::now();
  std::string counts;
  for (size_t i = 0; i < 100; ++i) {
    if (!QueryJson(".quant.countsJson", &counts, error)) return false;
  }
  const double query_ms = std::chrono::duration<double, std::milli>(
                              std::chrono::steady_clock::now() - query_started)
                              .count();
  oss << std::fixed << std::setprecision(3);
  oss << "events=" << count << " mode=synchronous_unbatched"
      << " ingest_p50_ms=" << Percentile(latencies_ms, 0.50)
      << " ingest_p95_ms=" << Percentile(latencies_ms, 0.95)
      << " ingest_p99_ms=" << Percentile(latencies_ms, 0.99)
      << " ingest_throughput_per_s="
      << static_cast<double>(count) /
             (std::accumulate(latencies_ms.begin(), latencies_ms.end(), 0.0) /
              1000.0)
      << " mean_query_ms=" << query_ms / 100.0 << " final_counts=" << counts
      << "\n";
  oss << "NOT IMPLEMENTED: bounded async queue, batched IPC publishing and "
         "backpressure measurement.\n";
  *report = oss.str();
  return true;
}

}  // namespace quant
