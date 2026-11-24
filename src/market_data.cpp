#include "quant.hpp"

#include <chrono>
#include <cctype>
#include <ctime>
#include <fstream>
#include <iomanip>
#include <limits>
#include <map>
#include <set>
#include <sstream>

namespace quant {
namespace {

constexpr const char* kUpstoxBase = "https://api.upstox.com/v2";
constexpr const char* kAssetsBase =
    "https://assets.upstox.com/market-quote/instruments/exchange";
constexpr const char* kFyersBase = "https://api-t1.fyers.in/api/v3";

std::string UrlEncode(std::string_view value) {
  static constexpr char kHex[] = "0123456789ABCDEF";
  std::string out;
  for (unsigned char c : value) {
    if (std::isalnum(c) || c == '-' || c == '_' || c == '.' || c == '~') {
      out.push_back(static_cast<char>(c));
    } else {
      out.push_back('%');
      out.push_back(kHex[c >> 4]);
      out.push_back(kHex[c & 0x0f]);
    }
  }
  return out;
}

std::string CsvEscape(std::string value) {
  if (value.find_first_of(",\"\n") == std::string::npos) return value;
  std::string out = "\"";
  for (char c : value) {
    if (c == '"')
      out += "\"\"";
    else
      out.push_back(c);
  }
  return out + "\"";
}

bool WriteUniverseCsv(const std::string& path,
                      const std::vector<Instrument>& instruments,
                      std::string* error) {
  std::ostringstream oss;
  oss << "symbol,exchange,isin,provider_key,name,security_type,tick_size,"
         "cas_eligible,eligible,reason\n";
  for (const Instrument& instrument : instruments) {
    oss << CsvEscape(instrument.symbol) << ',' << instrument.exchange << ','
        << instrument.isin << ',' << CsvEscape(instrument.provider_key) << ','
        << CsvEscape(instrument.name) << ',' << instrument.security_type << ','
        << instrument.tick_size << ','
        << (instrument.cas_eligible ? "true" : "false") << ','
        << (instrument.eligible ? "true" : "false") << ','
        << CsvEscape(instrument.eligibility_reason) << '\n';
  }
  const std::string error_text = WriteTextFile(path, oss.str());
  if (!error_text.empty()) {
    if (error) *error = error_text;
    return false;
  }
  return true;
}

std::string IndexKey(const std::string& symbol) {
  if (symbol == "NIFTY 50") return "NSE_INDEX|Nifty 50";
  if (symbol == "NIFTY BANK" || symbol == "BANKNIFTY")
    return "NSE_INDEX|Nifty Bank";
  if (symbol == "INDIA VIX") return "NSE_INDEX|India VIX";
  if (symbol == "GIFT NIFTY") return "GLOBAL_INDEX|SGX NIFTY";
  return "";
}

std::vector<Instrument> ParseInstruments(const Json& root) {
  std::vector<Instrument> instruments;
  if (root.type() != Json::Type::kArray) return instruments;
  for (const Json& item : root.array()) {
    if (item.Find("segment") == nullptr ||
        item.Find("segment")->as_string() != "NSE_EQ") {
      continue;
    }
    if (item.Find("instrument_type") == nullptr ||
        item.Find("instrument_type")->as_string() != "EQ") {
      continue;
    }
    const Json* symbol = item.Find("trading_symbol");
    const Json* key = item.Find("instrument_key");
    if (symbol == nullptr || key == nullptr) continue;
    Instrument instrument;
    instrument.symbol = symbol->as_string();
    instrument.provider_key = key->as_string();
    instrument.isin =
        item.Find("isin") != nullptr ? item.Find("isin")->as_string() : "";
    instrument.name =
        item.Find("name") != nullptr ? item.Find("name")->as_string() : "";
    instrument.security_type = item.Find("security_type") != nullptr
                                   ? item.Find("security_type")->as_string()
                                   : "";
    instrument.tick_size = item.Find("tick_size") != nullptr
                               ? item.Find("tick_size")->as_double(0.0)
                               : 0.0;
    instrument.freeze_quantity =
        item.Find("freeze_quantity") != nullptr
            ? item.Find("freeze_quantity")->as_double(0.0)
            : 0.0;
    instrument.cas_eligible = item.Find("cas_eligible") != nullptr &&
                              item.Find("cas_eligible")->as_bool(false);
    instruments.push_back(instrument);
  }
  std::sort(instruments.begin(), instruments.end(),
            [](const Instrument& a, const Instrument& b) {
              return a.symbol < b.symbol;
            });
  return instruments;
}

bool DownloadGzipJson(const HttpClient& http, const std::string& url, Json* out,
                      std::string* error) {
  const HttpResponse response = http.Get(url, {});
  if (!response.error.empty()) {
    if (error) *error = "HTTP error (" + response.error + ")";
    return false;
  }
  if (response.status != 200) {
    if (error) *error = "HTTP status " + std::to_string(response.status);
    return false;
  }
  const std::vector<uint8_t> bytes(response.body.begin(), response.body.end());
  const std::vector<uint8_t> decoded = Gunzip(bytes, error);
  if (decoded.empty()) return false;
  return Json::Parse(
      std::string_view(reinterpret_cast<const char*>(decoded.data()),
                       decoded.size()),
      out, error);
}

}  // namespace

// ---------------------------------------------------------------------------
// Normalisation helpers.
// ---------------------------------------------------------------------------
Quote NormalizeUpstoxQuote(const Json& item, const std::string& symbol,
                           const std::string& provider_key) {
  Quote quote;
  quote.symbol = symbol;
  quote.provider_key = provider_key;
  quote.source = Provider::kUpstox;
  quote.receive_timestamp_ms =
      std::chrono::duration_cast<std::chrono::milliseconds>(
          std::chrono::steady_clock::now().time_since_epoch())
          .count();
  const Json* last = item.Find("last_price");
  quote.last = last == nullptr ? 0.0 : last->as_double(0.0);
  const Json* volume = item.Find("volume");
  quote.volume = volume == nullptr ? 0.0 : volume->as_double(0.0);
  const Json* time = item.Find("last_trade_time");
  quote.exchange_timestamp_ms = time == nullptr ? 0 : time->as_int(0);
  quote.provider_timestamp_ms = quote.exchange_timestamp_ms;
  if (const Json* ohlc = item.Find("ohlc")) {
    quote.open = ohlc->Find("open") != nullptr
                     ? ohlc->Find("open")->as_double(0.0)
                     : 0.0;
    quote.high = ohlc->Find("high") != nullptr
                     ? ohlc->Find("high")->as_double(0.0)
                     : 0.0;
    quote.low =
        ohlc->Find("low") != nullptr ? ohlc->Find("low")->as_double(0.0) : 0.0;
    quote.close = ohlc->Find("close") != nullptr
                      ? ohlc->Find("close")->as_double(0.0)
                      : 0.0;
  }
  if (const Json* depth = item.Find("depth")) {
    if (const Json* buy = depth->Find("buy");
        buy != nullptr && !buy->array().empty()) {
      quote.bid = buy->array().front().Find("price") != nullptr
                      ? buy->array().front().Find("price")->as_double(0.0)
                      : 0.0;
      quote.bid_qty = buy->array().front().Find("quantity") != nullptr
                          ? buy->array().front().Find("quantity")->as_int(0)
                          : 0;
    }
    if (const Json* sell = depth->Find("sell");
        sell != nullptr && !sell->array().empty()) {
      quote.ask = sell->array().front().Find("price") != nullptr
                      ? sell->array().front().Find("price")->as_double(0.0)
                      : 0.0;
      quote.ask_qty = sell->array().front().Find("quantity") != nullptr
                          ? sell->array().front().Find("quantity")->as_int(0)
                          : 0;
    }
  }
  if (quote.bid <= 0.0 || quote.ask < quote.bid)
    quote.quality = "missing_depth";
  else
    quote.quality = "ok";
  return quote;
}

double SpreadBps(const Quote& quote) {
  if (!(quote.ask > 0.0) || !(quote.bid > 0.0) || quote.ask < quote.bid) {
    return std::numeric_limits<double>::infinity();
  }
  const double mid = (quote.ask + quote.bid) / 2.0;
  return mid > 0.0 ? (quote.ask - quote.bid) / mid * 10000.0
                   : std::numeric_limits<double>::infinity();
}

bool IsStale(const Quote& quote, int64_t now_ms, int max_age_seconds) {
  if (quote.exchange_timestamp_ms <= 0) return true;
  return now_ms - quote.exchange_timestamp_ms >
         static_cast<int64_t>(max_age_seconds) * 1000;
}

std::vector<Instrument> ApplyEligibility(
    std::vector<Instrument> instruments,
    const std::vector<Instrument>& suspended, const Config& config) {
  std::set<std::string> suspended_isins;
  for (const Instrument& instrument : suspended) {
    if (!instrument.isin.empty()) suspended_isins.insert(instrument.isin);
  }
  for (Instrument& instrument : instruments) {
    std::string reason = "eligible";
    bool eligible = true;
    if (instrument.security_type != "NORMAL") {
      eligible = false;
      reason = "non-normal security type";
    } else if (instrument.symbol.find('&') != std::string::npos) {
      eligible = false;
      reason = "non-standard symbol";
    } else if (!instrument.isin.empty() &&
               suspended_isins.count(instrument.isin) > 0) {
      eligible = false;
      reason = "suspended";
    } else if (!(instrument.tick_size > 0.0)) {
      eligible = false;
      reason = "invalid tick size";
    }
    instrument.eligible = eligible;
    instrument.eligibility_reason = reason;
  }
  (void)config;
  return instruments;
}

// ---------------------------------------------------------------------------
// MarketDataService.
// ---------------------------------------------------------------------------
MarketDataService::MarketDataService(Config config, Journal* journal)
    : config_(std::move(config)), journal_(journal) {}

std::vector<FeedStatus> MarketDataService::CheckFeeds() const {
  std::vector<FeedStatus> statuses;
  const HttpClient http(config_.http_timeout_seconds);

  FeedStatus upstox;
  upstox.provider = Provider::kUpstox;
  const Secret token = ResolveSecret(config_, "UPSTOX_ANALYTICS_TOKEN");
  if (token.value.empty()) {
    upstox.detail = "credential absent (service name checked only)";
  } else {
    const HttpResponse response =
        http.Get(std::string(kUpstoxBase) + "/market/status/NSE",
                 {{"Authorization", "Bearer " + token.value},
                  {"Accept", "application/json"}});
    upstox.latency_ms = response.elapsed_ms;
    Json root;
    std::string error;
    if (!response.error.empty()) {
      upstox.detail = "transport error: " + response.error;
    } else if (!Json::Parse(response.body, &root, &error)) {
      upstox.detail = "invalid response: " + error;
    } else {
      const Json* status = root.Find("status");
      const Json* data = root.Find("data");
      const Json* market = data == nullptr ? nullptr : data->Find("status");
      if (status != nullptr && status->as_string() == "success" &&
          market != nullptr) {
        upstox.available = true;
        upstox.detail = "read-only analytics token working; NSE status=" +
                        market->as_string();
      } else {
        upstox.detail = "provider rejected token or malformed response";
      }
    }
  }
  statuses.push_back(upstox);

  FeedStatus fyers;
  fyers.provider = Provider::kFyers;
  const Secret app_id = ResolveSecret(config_, "FYERS_APP_ID");
  const Secret access = ResolveSecret(config_, "FYERS_ACCESS_TOKEN");
  if (app_id.value.empty() || access.value.empty()) {
    fyers.detail = "credential absent (service name checked only)";
  } else {
    const HttpResponse response =
        http.Get(std::string(kFyersBase) + "/profile",
                 {{"Authorization", app_id.value + ":" + access.value}});
    fyers.latency_ms = response.elapsed_ms;
    Json root;
    std::string error;
    if (!response.error.empty()) {
      fyers.detail = "transport error: " + response.error;
    } else if (!Json::Parse(response.body, &root, &error)) {
      fyers.detail = "invalid response: " + error;
    } else {
      const Json* status = root.Find("s");
      if (status != nullptr && status->as_string() == "ok") {
        fyers.available = true;
        fyers.detail = "profile reachable";
      } else {
        const Json* code = root.Find("code");
        const Json* message = root.Find("message");
        fyers.detail =
            "token unusable: code=" +
            (code == nullptr ? "?" : std::to_string(code->as_int(0))) +
            " message=" + (message == nullptr ? "?" : message->as_string());
      }
    }
  }
  statuses.push_back(fyers);
  return statuses;
}

UniverseResult MarketDataService::SyncUniverse() const {
  UniverseResult result;
  const HttpClient http(config_.http_timeout_seconds);
  Json instruments_json;
  if (!DownloadGzipJson(http, std::string(kAssetsBase) + "/NSE.json.gz",
                        &instruments_json, &result.error)) {
    return result;
  }
  Json suspended_json;
  std::string suspended_error;
  if (!DownloadGzipJson(
          http, std::string(kAssetsBase) + "/suspended-instrument.json.gz",
          &suspended_json, &suspended_error)) {
    suspended_json = Json::Array({});
  }
  result.instruments = ParseInstruments(instruments_json);
  result.total_fetched = instruments_json.type() == Json::Type::kArray
                             ? instruments_json.array().size()
                             : 0;
  result.suspended = ParseInstruments(suspended_json);
  result.instruments = ApplyEligibility(std::move(result.instruments),
                                        result.suspended, config_);
  result.source = "Upstox BOD NSE.json.gz (public, refreshed daily ~06:00 IST)";
  if (!WriteUniverseCsv(config_.universe_path, result.instruments,
                        &result.error)) {
    return result;
  }

  if (journal_ != nullptr) {
    Json::Object payload;
    payload["total_fetched"] =
        Json::Number(static_cast<double>(result.total_fetched));
    size_t eligible_count = 0;
    for (const Instrument& instrument : result.instruments) {
      if (instrument.eligible) ++eligible_count;
    }
    payload["eligible"] = Json::Number(static_cast<double>(eligible_count));
    payload["normalised"] =
        Json::Number(static_cast<double>(result.instruments.size()));
    payload["source"] = Json::String(result.source);
    Event event;
    event.type = EventType::kHeartbeat;
    event.payload = Json::ObjectValue(payload).Dump();
    std::string journal_error;
    journal_->Append(&event, &journal_error);
  }
  return result;
}

bool MarketDataService::SyncHistory(const std::vector<Instrument>& instruments,
                                    std::string* error) const {
  const Secret token = ResolveSecret(config_, "UPSTOX_ANALYTICS_TOKEN");
  if (token.value.empty()) {
    if (error) *error = "Upstox analytics token absent";
    return false;
  }
  const HttpClient http(config_.http_timeout_seconds);
  const std::time_t now_time = std::time(nullptr);
  std::tm utc{};
  gmtime_r(&now_time, &utc);
  std::ostringstream to_stream;
  to_stream << std::put_time(&utc, "%Y-%m-%d");
  const std::string to_date =
      to_stream.str();  // Provider rejects future dates safely.
  std::tm from = utc;
  from.tm_year -= 2;
  std::ostringstream from_stream;
  from_stream << std::put_time(&from, "%Y-%m-%d");
  const std::string from_date = from_stream.str();
  size_t fetched = 0;
  std::vector<std::string> failures;
  std::vector<Instrument> targets;

  std::set<std::string> seed(config_.seed_symbols.begin(),
                             config_.seed_symbols.end());
  for (const Instrument& instrument : instruments) {
    if (instrument.eligible && seed.count(instrument.symbol) > 0) {
      targets.push_back(instrument);
    }
  }
  if (targets.empty()) {
    for (const Instrument& instrument : instruments) {
      if (instrument.eligible) targets.push_back(instrument);
      if (targets.size() >= config_.universe_limit) break;
    }
  }

  for (const Instrument& instrument : targets) {
    const std::string url = std::string(kUpstoxBase) + "/historical-candle/" +
                            UrlEncode(instrument.provider_key) + "/day/" +
                            to_date + "/" + from_date;
    const HttpResponse response =
        http.Get(url, {{"Authorization", "Bearer " + token.value},
                       {"Accept", "application/json"}});
    Json root;
    std::string parse_error;
    if (!response.error.empty() || response.status != 200 ||
        !Json::Parse(response.body, &root, &parse_error)) {
      failures.push_back(instrument.symbol);
      continue;
    }
    const Json* data = root.Find("data");
    const Json* candles = data == nullptr ? nullptr : data->Find("candles");
    if (candles == nullptr || candles->array().empty()) {
      failures.push_back(instrument.symbol);
      continue;
    }
    std::ostringstream csv;
    csv << "date,open,high,low,close,volume\n";
    for (const Json& row : candles->array()) {
      if (row.type() != Json::Type::kArray || row.array().size() < 6) continue;
      const std::string iso = row.array()[0].as_string();
      csv << (iso.size() >= 10 ? iso.substr(0, 10) : iso) << ','
          << row.array()[1].as_double(0.0) << ','
          << row.array()[2].as_double(0.0) << ','
          << row.array()[3].as_double(0.0) << ','
          << row.array()[4].as_double(0.0) << ','
          << row.array()[5].as_double(0.0) << '\n';
    }
    const std::string path =
        config_.history_dir + "/" + instrument.symbol + ".csv";
    if (WriteTextFile(path, csv.str()).empty()) ++fetched;
  }

  for (const std::string& symbol : config_.index_symbols) {
    const std::string key = IndexKey(symbol);
    if (key.empty()) continue;
    const std::string url = std::string(kUpstoxBase) + "/historical-candle/" +
                            UrlEncode(key) + "/day/" + to_date + "/" +
                            from_date;
    const HttpResponse response =
        http.Get(url, {{"Authorization", "Bearer " + token.value},
                       {"Accept", "application/json"}});
    Json root;
    if (!Json::Parse(response.body, &root, nullptr) || response.status != 200)
      continue;
    const Json* data = root.Find("data");
    const Json* candles = data == nullptr ? nullptr : data->Find("candles");
    if (candles == nullptr) continue;
    std::ostringstream csv;
    csv << "date,open,high,low,close,volume\n";
    for (const Json& row : candles->array()) {
      if (row.type() != Json::Type::kArray || row.array().size() < 6) continue;
      const std::string iso = row.array()[0].as_string();
      csv << (iso.size() >= 10 ? iso.substr(0, 10) : iso) << ','
          << row.array()[1].as_double(0.0) << ','
          << row.array()[2].as_double(0.0) << ','
          << row.array()[3].as_double(0.0) << ','
          << row.array()[4].as_double(0.0) << ','
          << row.array()[5].as_double(0.0) << '\n';
    }
    WriteTextFile(config_.history_dir + "/" + symbol + ".csv", csv.str());
  }

  if (fetched == 0) {
    if (error)
      *error = "no historical series fetched; failures=" +
               std::to_string(failures.size());
    return false;
  }
  if (error) {
    *error = "fetched=" + std::to_string(fetched) +
             " failed=" + std::to_string(failures.size());
  }
  return true;
}

bool MarketDataService::FetchQuotes(const std::vector<Instrument>& instruments,
                                    std::vector<Quote>* quotes,
                                    std::string* error) const {
  quotes->clear();
  const Secret token = ResolveSecret(config_, "UPSTOX_ANALYTICS_TOKEN");
  if (token.value.empty()) {
    if (error) *error = "Upstox analytics token absent";
    return false;
  }
  const HttpClient http(config_.http_timeout_seconds);
  for (size_t offset = 0; offset < instruments.size(); offset += 400) {
    std::string keys;
    for (size_t i = offset; i < std::min(offset + 400, instruments.size());
         ++i) {
      if (!keys.empty()) keys += ",";
      keys += UrlEncode(instruments[i].provider_key);
    }
    const HttpResponse response =
        http.Get(std::string(kUpstoxBase) +
                     "/market-quote/quotes?instrument_key=" + keys,
                 {{"Authorization", "Bearer " + token.value},
                  {"Accept", "application/json"}});
    Json root;
    if (!response.error.empty() || response.status != 200 ||
        !Json::Parse(response.body, &root, error)) {
      if (error) *error = "quote request failed: " + response.error;
      return false;
    }
    const Json* data = root.Find("data");
    if (data == nullptr || data->type() != Json::Type::kObject) continue;
    for (const auto& [key, item] : data->object()) {
      std::string symbol = key;
      const size_t colon = symbol.find(':');
      if (colon != std::string::npos) symbol = symbol.substr(colon + 1);
      quotes->push_back(NormalizeUpstoxQuote(item, symbol, key));
    }
  }
  return true;
}

bool MarketDataService::MarketStatus(std::string* status,
                                     std::string* error) const {
  const Secret token = ResolveSecret(config_, "UPSTOX_ANALYTICS_TOKEN");
  if (token.value.empty()) {
    *status = "UNKNOWN";
    if (error) *error = "Upstox analytics token absent";
    return false;
  }
  const HttpClient http(config_.http_timeout_seconds);
  const HttpResponse response =
      http.Get(std::string(kUpstoxBase) + "/market/status/NSE",
               {{"Authorization", "Bearer " + token.value},
                {"Accept", "application/json"}});
  Json root;
  if (!Json::Parse(response.body, &root, error) || response.status != 200) {
    *status = "UNKNOWN";
    return false;
  }
  const Json* data = root.Find("data");
  const Json* market = data == nullptr ? nullptr : data->Find("status");
  *status = market == nullptr ? "UNKNOWN" : market->as_string();
  return true;
}

bool MarketDataService::GlobalIndices(std::vector<Quote>* quotes,
                                      std::string* error) const {
  quotes->clear();
  const Secret token = ResolveSecret(config_, "UPSTOX_ANALYTICS_TOKEN");
  if (token.value.empty()) {
    if (error) *error = "Upstox analytics token absent";
    return false;
  }
  const HttpClient http(config_.http_timeout_seconds);
  Json global;
  if (!DownloadGzipJson(http, std::string(kAssetsBase) + "/global.json.gz",
                        &global, error)) {
    return false;
  }
  std::vector<std::string> keys;
  if (global.type() == Json::Type::kArray) {
    for (const Json& item : global.array()) {
      const Json* key = item.Find("instrument_key");
      if (key == nullptr) continue;
      const std::string value = key->as_string();
      if (value.find("GIFT") != std::string::npos ||
          value.find("SGX") != std::string::npos) {
        keys.push_back(value);
      }
    }
  }
  if (keys.empty()) return true;
  std::string joined;
  for (const std::string& key : keys) {
    if (!joined.empty()) joined += ",";
    joined += UrlEncode(key);
  }
  const HttpResponse response =
      http.Get(std::string(kUpstoxBase) +
                   "/market-quote/quotes?instrument_key=" + joined,
               {{"Authorization", "Bearer " + token.value},
                {"Accept", "application/json"}});
  Json root;
  if (!Json::Parse(response.body, &root, error)) return false;
  const Json* data = root.Find("data");
  if (data == nullptr) return false;
  for (const auto& [key, item] : data->object()) {
    quotes->push_back(NormalizeUpstoxQuote(item, "GIFT NIFTY", key));
  }
  return true;
}

}  // namespace quant
