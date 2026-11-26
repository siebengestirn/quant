#include "quant.hpp"

#include <fcntl.h>
#include <sys/stat.h>
#include <filesystem>
#include <unistd.h>

#include <chrono>
#include <cmath>
#include <cstdio>
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
  if (value.find_first_of(",\"\n") == std::string::npos)
    return value;
  std::string out = "\"";
  for (char c : value) {
    if (c == '"')
      out += "\"\"";
    else
      out.push_back(c);
  }
  return out + "\"";
}

std::string UniverseCsvContent(const std::vector<Instrument>& instruments) {
  std::ostringstream oss;
  oss << "symbol,exchange,isin,provider_key,name,security_type,tick_size,"
         "cas_eligible,eligible,reason,instrument_class\n";
  for (const Instrument& instrument : instruments) {
    oss << CsvEscape(instrument.symbol) << ',' << instrument.exchange << ','
        << instrument.isin << ',' << CsvEscape(instrument.provider_key) << ','
        << CsvEscape(instrument.name) << ',' << instrument.security_type << ','
        << instrument.tick_size << ','
        << (instrument.cas_eligible ? "true" : "false") << ','
        << (instrument.eligible ? "true" : "false") << ','
        << CsvEscape(instrument.eligibility_reason) << ','
        << static_cast<int>(instrument.instrument_class) << '\n';
  }
  return oss.str();
}

[[maybe_unused]] bool WriteUniverseCsv(
    const std::string& path,
    const std::vector<Instrument>& instruments,
    std::string* error) {
  const std::string error_text =
      WriteTextFile(path, UniverseCsvContent(instruments));
  if (!error_text.empty()) {
    if (error)
      *error = error_text;
    return false;
  }
  return true;
}

std::string IndexKey(const std::string& symbol) {
  if (symbol == "NIFTY 50")
    return "NSE_INDEX|Nifty 50";
  if (symbol == "NIFTY BANK" || symbol == "BANKNIFTY")
    return "NSE_INDEX|Nifty Bank";
  if (symbol == "INDIA VIX")
    return "NSE_INDEX|India VIX";
  if (symbol == "GIFT NIFTY")
    return "GLOBAL_INDEX|SGX NIFTY";
  return "";
}

std::vector<Instrument> ParseInstruments(const Json& root) {
  std::vector<Instrument> instruments;
  if (root.type() != Json::Type::kArray)
    return instruments;
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
    if (symbol == nullptr || key == nullptr)
      continue;
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
    if (instrument.security_type == "NORMAL") {
      instrument.instrument_class = InstrumentClass::kEquity;
    } else if (instrument.security_type == "SME") {
      instrument.instrument_class = InstrumentClass::kSme;
    } else if (item.Find("segment")->as_string().find("INDEX") !=
               std::string::npos) {
      instrument.instrument_class = InstrumentClass::kIndex;
    } else {
      instrument.instrument_class = InstrumentClass::kOther;
    }
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
  std::sort(instruments.begin(),
            instruments.end(),
            [](const Instrument& a, const Instrument& b) {
              return a.symbol < b.symbol;
            });
  return instruments;
}

bool DownloadGzipJson(const HttpClient& http,
                      const std::string& url,
                      Json* out,
                      std::string* error) {
  const HttpResponse response = http.Get(url, {});
  if (!response.error.empty()) {
    if (error)
      *error = "HTTP error (" + response.error + ")";
    return false;
  }
  if (response.status != 200) {
    if (error)
      *error = "HTTP status " + std::to_string(response.status);
    return false;
  }
  const std::vector<uint8_t> bytes(response.body.begin(), response.body.end());
  const std::vector<uint8_t> decoded = Gunzip(bytes, error);
  if (decoded.empty())
    return false;
  return Json::Parse(
      std::string_view(reinterpret_cast<const char*>(decoded.data()),
                       decoded.size()),
      out,
      error);
}

}  // namespace

namespace {

// Universe audit identity uses the same SHA-256 algorithm as the dataset
// contract, so a content digest has one meaning across the whole system.
std::string UniverseAuditDigest(const std::string& content) {
  return Sha256Hex(content);
}

bool AppendUniverseAudit(Journal* journal,
                         const char* action,
                         const std::string& transaction_id,
                         const std::string& digest,
                         size_t records,
                         std::string* error) {
  if (journal == nullptr) {
    if (error)
      *error = "universe publication requires an audit journal";
    return false;
  }
  Json::Object payload;
  payload["action"] = Json::String(action);
  payload["transaction_id"] = Json::String(transaction_id);
  payload["content_digest"] = Json::String(digest);
  payload["schema_version"] = Json::UInteger(1);
  payload["records"] = Json::UInteger(static_cast<uint64_t>(records));
  Event event;
  event.type = EventType::kHeartbeat;
  event.payload = Json::ObjectValue(payload).Dump();
  return journal->Append(&event, error);
}

bool BuildUniverseTransactions(Journal* journal,
                               std::map<std::string, UniverseTransaction>* out,
                               std::string* error) {
  if (journal == nullptr || out == nullptr) {
    if (error)
      *error = "transaction reconstruction requires a journal";
    return false;
  }
  out->clear();
  auto fail = [error](const std::string& reason) {
    if (error)
      *error = reason;
    return false;
  };
  const std::vector<Event> events = journal->Replay(error);
  if (error != nullptr && !error->empty())
    return false;
  for (const Event& event : events) {
    if (event.type != EventType::kHeartbeat)
      continue;
    Json payload;
    if (!Json::Parse(event.payload, &payload, error))
      return fail("universe transaction record is not valid JSON");
    const Json* action = payload.Find("action");
    const Json* tx = payload.Find("transaction_id");
    const Json* digest = payload.Find("content_digest");
    const Json* size = payload.Find("records");
    if (action == nullptr || tx == nullptr || digest == nullptr ||
        size == nullptr || action->type() != Json::Type::kString ||
        tx->type() != Json::Type::kString ||
        digest->type() != Json::Type::kString || !size->is_exact_integer() ||
        (action->as_string() != "UNIVERSE_PUBLISH" &&
         action->as_string() != "UNIVERSE_PUBLISH_COMMIT"))
      return fail("malformed universe transaction record");
    const std::string id = tx->as_string();
    const std::string value = digest->as_string();
    const int64_t records = size->as_int(0);
    if (id.empty() || value.empty() || records < 0)
      return fail("malformed universe transaction identity");
    UniverseTransaction& transaction = (*out)[id];
    if (transaction.id.empty())
      transaction.id = id;
    if (action->as_string() == "UNIVERSE_PUBLISH") {
      if (transaction.published)
        return fail("duplicate publish-begin record for one transaction");
      if (transaction.committed &&
          (transaction.digest != value ||
           transaction.size != static_cast<size_t>(records)))
        return fail("committed transaction id reused with a different payload");
      transaction.digest = value;
      transaction.size = static_cast<size_t>(records);
      transaction.published = true;
      ++transaction.publishes;
    } else {
      if (!transaction.published)
        return fail("commit without a publish record");
      if (transaction.digest != value ||
          transaction.size != static_cast<size_t>(records))
        return fail("commit record conflicts with its publish record");
      transaction.committed = true;
      ++transaction.commits;
      if (transaction.commits > 1)
        return fail("duplicate commit record for one transaction");
    }
  }
  return true;
}

bool SyncDirectory(const std::string& path, std::string* error) {
  const size_t slash = path.find_last_of('/');
  const std::string directory =
      slash == std::string::npos ? "." : path.substr(0, slash);
  const int fd = open(directory.c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC);
  if (fd < 0 || fsync(fd) != 0) {
    if (fd >= 0)
      close(fd);
    if (error)
      *error = "universe directory synchronization failed";
    return false;
  }
  close(fd);
  return true;
}

}  // namespace

bool ValidTransactionId(std::string_view id) {
  if (id.empty() || id.size() > 64)
    return false;
  const unsigned char first = static_cast<unsigned char>(id.front());
  if (!(std::isalnum(first) || id.front() == '_'))
    return false;
  for (char c : id) {
    const unsigned char value = static_cast<unsigned char>(c);
    if (!(std::isalnum(value) || c == '_' || c == '-'))
      return false;
  }
  return true;
}

bool WriteStagedExclusive(const std::string& directory,
                          const std::string& name,
                          const std::string& content,
                          std::string* error) {
  auto fail = [error](const char* reason) {
    if (error)
      *error = reason;
    return false;
  };
  int dirfd = open(directory.c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC);
  if (dirfd < 0)
    return fail("cannot open publication directory");
  int fd = openat(dirfd,
                  name.c_str(),
                  O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC,
                  0600);
  if (fd < 0) {
    close(dirfd);
    return fail(("exclusive staging creation failed: " +
                 std::string(std::strerror(errno)))
                    .c_str());
  }
  struct stat metadata{};
  if (fstat(fd, &metadata) != 0 || !S_ISREG(metadata.st_mode)) {
    close(fd);
    close(dirfd);
    return fail("staging object is not a regular file");
  }
  size_t written = 0;
  while (written < content.size()) {
    const ssize_t count =
        write(fd, content.data() + written, content.size() - written);
    if (count < 0 && errno == EINTR)
      continue;
    if (count <= 0) {
      close(fd);
      close(dirfd);
      return fail("staged write failed");
    }
    written += static_cast<size_t>(count);
  }
  const bool durable = fsync(fd) == 0;
  close(fd);
  const bool directory_durable = fsync(dirfd) == 0;
  close(dirfd);
  if (!durable || !directory_durable)
    return fail("staged fsync failed");
  return true;
}

bool RenameStaged(const std::string& directory,
                  const std::string& from,
                  const std::string& to,
                  std::string* error) {
  int dirfd = open(directory.c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC);
  if (dirfd < 0) {
    if (error)
      *error = "cannot open publication directory for rename";
    return false;
  }
  const bool ok = renameat(dirfd, from.c_str(), dirfd, to.c_str()) == 0 &&
                  fsync(dirfd) == 0;
  close(dirfd);
  if (!ok && error)
    *error = "descriptor-relative publication rename failed";
  return ok;
}

bool WriteAndSyncFile(const std::string& path,
                      const std::string& content,
                      std::string* error) {
  const int fd =
      open(path.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
  if (fd < 0) {
    if (error)
      *error = "cannot open staged universe file";
    return false;
  }
  size_t written = 0;
  while (written < content.size()) {
    const ssize_t count =
        write(fd, content.data() + written, content.size() - written);
    if (count < 0 && errno == EINTR)
      continue;
    if (count <= 0) {
      close(fd);
      if (error)
        *error = "staged universe write failed";
      return false;
    }
    written += static_cast<size_t>(count);
  }
  if (fsync(fd) != 0) {
    close(fd);
    if (error)
      *error = "staged universe fsync failed";
    return false;
  }
  if (close(fd) != 0) {
    if (error)
      *error = "staged universe close failed";
    return false;
  }
  return true;
}

bool PublishUniverse(const std::string& path,
                     const std::string& content,
                     Journal* journal,
                     const std::string& transaction_id,
                     PublicationFailurePoint inject,
                     std::string* error) {
  auto fail = [error](const std::string& reason) {
    if (error)
      *error = reason;
    return false;
  };
  if (!ValidTransactionId(transaction_id))
    return fail("publication transaction id violates canonical grammar");
  std::map<std::string, UniverseTransaction> known;
  if (!BuildUniverseTransactions(journal, &known, error)) {
    return false;
  }
  const std::string digest = UniverseAuditDigest(content);
  const auto prior = known.find(transaction_id);
  if (prior != known.end()) {
    if (prior->second.digest != digest || prior->second.size != content.size())
      return fail("transaction id reused with a different payload");
    if (prior->second.committed) {
      std::ifstream published(path, std::ios::binary);
      const std::string current((std::istreambuf_iterator<char>(published)),
                                std::istreambuf_iterator<char>());
      if (UniverseAuditDigest(current) != digest ||
          current.size() != content.size())
        return fail("committed transaction target was modified externally");
      return true;  // Idempotent retry: no rewrite and no new audit record.
    }
    return fail("pending transaction requires recovery, not a new publish");
  }
  const std::string staging_name = ".staging." + transaction_id;
  const size_t separator = path.find_last_of('/');
  const std::string directory =
      separator == std::string::npos ? "." : path.substr(0, separator);
  const std::string staged = directory + "/" + staging_name;
  if (!WriteStagedExclusive(directory, staging_name, content, error)) {
    return false;
  }
  const std::string staged_content = [](const std::string& p) {
    std::ifstream input(p, std::ios::binary);
    return std::string((std::istreambuf_iterator<char>(input)),
                       std::istreambuf_iterator<char>());
  }(staged);
  if (UniverseAuditDigest(staged_content) != digest ||
      staged_content.size() != content.size())
    return fail("staged content digest does not match transaction identity");
  if (!AppendUniverseAudit(journal,
                           "UNIVERSE_PUBLISH",
                           transaction_id,
                           digest,
                           content.size(),
                           error))
    return false;
  if (inject == PublicationFailurePoint::kAfterAuditBeforeRename)
    return false;
  const size_t final_separator = path.find_last_of('/');
  const std::string final_name = final_separator == std::string::npos
                                     ? path
                                     : path.substr(final_separator + 1);
  if (!RenameStaged(directory, staging_name, final_name, error)) {
    return false;
  }
  if (inject == PublicationFailurePoint::kAfterRenameBeforeCommit)
    return false;
  std::ifstream final_input(path, std::ios::binary);
  const std::string final_content((std::istreambuf_iterator<char>(final_input)),
                                  std::istreambuf_iterator<char>());
  if (UniverseAuditDigest(final_content) != digest ||
      final_content.size() != content.size())
    return fail("published content digest does not match transaction identity");
  return AppendUniverseAudit(journal,
                             "UNIVERSE_PUBLISH_COMMIT",
                             transaction_id,
                             digest,
                             content.size(),
                             error);
}

bool RecoverUniversePublication(const std::string& path,
                                Journal* journal,
                                UniverseRecoveryReport* report,
                                std::string* error) {
  auto fail = [error](const std::string& reason) {
    if (error)
      *error = reason;
    return false;
  };
  UniverseRecoveryReport local;
  std::map<std::string, UniverseTransaction> transactions;
  if (!BuildUniverseTransactions(journal, &transactions, error))
    return false;
  for (const auto& [id, transaction] : transactions) {
    if (transaction.committed) {
      ++local.committed;
      continue;
    }
    const size_t separator = path.find_last_of('/');
    const std::string directory =
        separator == std::string::npos ? "." : path.substr(0, separator);
    const std::string staged = directory + "/.staging." + id;
    bool published = false;
    std::string content;
    if (std::filesystem::exists(staged)) {
      std::ifstream input(staged, std::ios::binary);
      content.assign(std::istreambuf_iterator<char>(input),
                     std::istreambuf_iterator<char>());
      if (UniverseAuditDigest(content) == transaction.digest &&
          content.size() == transaction.size) {
        const size_t final_separator = path.find_last_of('/');
        const std::string final_name = final_separator == std::string::npos
                                           ? path
                                           : path.substr(final_separator + 1);
        if (!RenameStaged(directory, ".staging." + id, final_name, error))
          return false;
        published = true;
      }
    }
    if (!published && std::filesystem::exists(path)) {
      std::ifstream input(path, std::ios::binary);
      content.assign(std::istreambuf_iterator<char>(input),
                     std::istreambuf_iterator<char>());
      if (UniverseAuditDigest(content) == transaction.digest &&
          content.size() == transaction.size)
        published = true;
    }
    if (!published) {
      local.unresolved.push_back(id);
      continue;
    }
    if (!SyncDirectory(path, error))
      return false;
    if (!AppendUniverseAudit(journal,
                             "UNIVERSE_PUBLISH_COMMIT",
                             id,
                             transaction.digest,
                             transaction.size,
                             error))
      return false;
    ++local.recovered;
  }
  if (report)
    *report = local;
  if (!local.unresolved.empty())
    return fail("unresolved universe publication transactions: " +
                std::to_string(local.unresolved.size()));
  return true;
}

// ---------------------------------------------------------------------------
// Normalisation helpers.

// ---------------------------------------------------------------------------
Quote NormalizeUpstoxQuote(const Json& item,
                           const std::string& symbol,
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
  // Upstox provides distinct feed-update and last-trade times. Keep both.
  const Json* update = item.Find("timestamp");
  const Json* trade = item.Find("last_trade_time");
  quote.provider_timestamp_ms = update == nullptr ? 0 : update->as_int(0);
  quote.last_trade_timestamp_ms = trade == nullptr ? 0 : trade->as_int(0);
  quote.exchange_timestamp_ms = quote.last_trade_timestamp_ms;
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

bool ProviderFreshnessUnknown(const Quote& quote) {
  return quote.provider_timestamp_ms <= 0;
}

bool TradeFreshnessUnknown(const Quote& quote) {
  return quote.last_trade_timestamp_ms <= 0;
}

bool IsStaleProviderUpdate(const Quote& quote,
                           int64_t now_ms,
                           int max_age_seconds) {
  // Missing provider time is UNKNOWN, never silently inferred from last trade.
  if (ProviderFreshnessUnknown(quote))
    return false;
  return now_ms - quote.provider_timestamp_ms >
         static_cast<int64_t>(max_age_seconds) * 1000;
}

bool IsStaleTrade(const Quote& quote, int64_t now_ms, int max_age_seconds) {
  if (TradeFreshnessUnknown(quote))
    return false;
  return now_ms - quote.last_trade_timestamp_ms >
         static_cast<int64_t>(max_age_seconds) * 1000;
}

bool IsStale(const Quote& quote, int64_t now_ms, int max_age_seconds) {
  return IsStaleProviderUpdate(quote, now_ms, max_age_seconds);
}

std::vector<Instrument> ApplyEligibility(
    std::vector<Instrument> instruments,
    const std::vector<Instrument>& suspended,
    bool status_data_available,
    const Config& config) {
  std::set<std::string> suspended_isins;
  for (const Instrument& instrument : suspended) {
    if (!instrument.isin.empty())
      suspended_isins.insert(instrument.isin);
  }
  for (Instrument& instrument : instruments) {
    std::string reason = "eligible";
    bool eligible = true;
    if (!status_data_available) {
      eligible = false;
      reason =
          "INELIGIBLE_PENDING_VERIFICATION: surveillance status unavailable";
    } else if (instrument.instrument_class != InstrumentClass::kEquity) {
      eligible = false;
      reason = "not an ordinary equity";
    } else if (instrument.security_type != "NORMAL") {
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
    : config_(std::move(config)), journal_(journal) {
}

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
  if (!DownloadGzipJson(http,
                        std::string(kAssetsBase) + "/NSE.json.gz",
                        &instruments_json,
                        &result.error)) {
    return result;
  }
  Json suspended_json;
  std::string suspended_error;
  bool status_data_available = true;
  if (!DownloadGzipJson(
          http,
          std::string(kAssetsBase) + "/suspended-instrument.json.gz",
          &suspended_json,
          &suspended_error)) {
    suspended_json = Json::Array({});
    status_data_available = false;
  }
  result.instruments = ParseInstruments(instruments_json);
  result.total_fetched = instruments_json.type() == Json::Type::kArray
                             ? instruments_json.array().size()
                             : 0;
  result.suspended = ParseInstruments(suspended_json);
  result.instruments = ApplyEligibility(std::move(result.instruments),
                                        result.suspended,
                                        status_data_available,
                                        config_);
  result.source = "Upstox BOD NSE.json.gz (public, refreshed daily ~06:00 IST)";
  const std::string content = UniverseCsvContent(result.instruments);
  const std::string transaction_id =
      "UNIV-" +
      std::to_string(static_cast<uint64_t>(
          std::chrono::duration_cast<std::chrono::milliseconds>(
              std::chrono::system_clock::now().time_since_epoch())
              .count())) +
      "-" + ChecksumHex(content);

  if (!PublishUniverse(config_.universe_path,
                       content,
                       journal_,
                       transaction_id,
                       PublicationFailurePoint::kNone,
                       &result.error))
    return result;
  return result;
}

namespace {

bool StrictNumber(const Json& value, double* out) {
  if (!value.is_exact_integer() && value.type() != Json::Type::kNumber)
    return false;
  const double number = value.as_double(0.0);
  if (!std::isfinite(number))
    return false;
  *out = number;
  return true;
}

}  // namespace

HistoryParseResult BuildHistoryCsv(const Json& candles,
                                   const std::string& symbol) {
  HistoryParseResult result;
  result.csv = "date,open,high,low,close,volume\n";
  std::set<std::string> dates;
  if (candles.type() != Json::Type::kArray || candles.array().empty()) {
    result.errors.push_back(symbol + ": no candles");
    return result;
  }
  for (const Json& row : candles.array()) {
    if (row.type() != Json::Type::kArray || row.array().size() != 6) {
      result.errors.push_back(symbol + ": row width mismatch");
      return result;
    }
    const Json& date_field = row.array()[0];
    if (date_field.type() != Json::Type::kString) {
      result.errors.push_back(symbol + ": non-string date");
      return result;
    }
    const std::string raw_date = date_field.as_string();
    const std::string date =
        raw_date.size() >= 10 ? raw_date.substr(0, 10) : raw_date;
    if (!IsCanonicalExchangeDate(date)) {
      result.errors.push_back(symbol + ": invalid calendar date " + date);
      return result;
    }
    if (!dates.insert(date).second) {
      result.errors.push_back(symbol + ": duplicate date " + date);
      return result;
    }
    double values[5] = {};
    for (size_t i = 0; i < 5; ++i) {
      if (!StrictNumber(row.array()[i + 1], &values[i])) {
        result.errors.push_back(symbol + ": non-numeric field");
        return result;
      }
    }
    const double open = values[0];
    const double high = values[1];
    const double low = values[2];
    const double close = values[3];
    const double volume = values[4];
    if (low <= 0.0 || close <= 0.0 || low > std::min(open, close) ||
        std::max(open, close) > high || volume < 0.0) {
      result.errors.push_back(symbol + ": invalid OHLCV relationship");
      return result;
    }
    std::ostringstream line;
    line.imbue(std::locale::classic());
    line << std::setprecision(std::numeric_limits<double>::max_digits10);
    line << date << ',' << open << ',' << high << ',' << low << ',' << close
         << ',' << volume << '\n';
    result.csv += line.str();
    ++result.accepted;
  }
  return result;
}

bool PublishHistoryFile(const std::string& path,
                        const std::string& content,
                        std::string* error) {
  const std::string staged = path + ".staging";
  if (!WriteAndSyncFile(staged, content, error))
    return false;
  if (!SyncDirectory(staged, error))
    return false;
  if (rename(staged.c_str(), path.c_str()) != 0) {
    if (error)
      *error = "history publication rename failed";
    return false;
  }
  return SyncDirectory(path, error);
}

HistorySyncResult MarketDataService::SyncHistory(
    const std::vector<Instrument>& instruments, std::string* error) const {
  const Secret token = ResolveSecret(config_, "UPSTOX_ANALYTICS_TOKEN");
  HistorySyncResult sync;
  if (token.value.empty()) {
    sync.status = HistorySyncStatus::kBlocked;
    if (error)
      *error = "Upstox analytics token absent";
    return sync;
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
      if (instrument.eligible)
        targets.push_back(instrument);
      if (targets.size() >= config_.universe_limit)
        break;
    }
  }

  DatasetExpectations contract;
  contract.snapshot_id =
      "SNAP-" +
      std::to_string(std::chrono::duration_cast<std::chrono::milliseconds>(
                         std::chrono::system_clock::now().time_since_epoch())
                         .count());
  contract.calendar_id = "NSE_CASH_DAILY_OBSERVED";
  contract.generated_at_ms =
      std::chrono::duration_cast<std::chrono::milliseconds>(
          std::chrono::system_clock::now().time_since_epoch())
          .count();
  contract.provenance = "UPSTOX_BOD_DAILY";
  contract.min_rows = 1;
  auto safe_identity = [](std::string value) {
    for (char& c : value) {
      const unsigned char byte = static_cast<unsigned char>(c);
      if (!(std::isalnum(byte) || c == '.' || c == '_' || c == ':' || c == '-'))
        c = '_';
    }
    return value.empty() ? std::string("UNKNOWN_ID") : value;
  };
  for (const Instrument& instrument : targets)
    contract.instrument_ids[instrument.symbol] = safe_identity(
        instrument.isin.empty() ? instrument.provider_key : instrument.isin);
  for (const std::string& symbol : config_.index_symbols)
    contract.instrument_ids[symbol] = safe_identity(IndexKey(symbol));
  for (const auto& [symbol, ignored] : contract.instrument_ids) {
    (void)ignored;
    contract.symbols.push_back(symbol);
  }
  std::set<std::string> observed_sessions;
  std::map<std::string, std::string> raw_digests;
  auto processSeries = [&](const std::string& symbol,
                           const std::string& key) -> bool {
    const std::string url = std::string(kUpstoxBase) + "/historical-candle/" +
                            UrlEncode(key) + "/day/" + to_date + "/" +
                            from_date;
    const HttpResponse response =
        http.Get(url,
                 {{"Authorization", "Bearer " + token.value},
                  {"Accept", "application/json"}});
    if (!response.error.empty() || response.status != 200) {
      failures.push_back(symbol + ": http");
      return false;
    }
    std::filesystem::create_directories(config_.history_raw_dir);
    std::filesystem::create_directories(config_.history_dir);
    const std::string raw_path = config_.history_raw_dir + "/" + symbol + "_" +
                                 Sha256Hex(response.body) + ".json";
    std::string raw_error;
    if (!WriteAndSyncFile(raw_path, response.body, &raw_error)) {
      failures.push_back(symbol + ": raw capture failed");
      return false;
    }
    Json root;
    std::string parse_error;
    if (!Json::Parse(response.body, &root, &parse_error)) {
      failures.push_back(symbol + ": response parse failed");
      return false;
    }
    const Json* data = root.Find("data");
    const Json* candles = data == nullptr ? nullptr : data->Find("candles");
    if (candles == nullptr) {
      failures.push_back(symbol + ": no candles");
      return false;
    }
    HistoryParseResult parsed = BuildHistoryCsv(*candles, symbol);
    if (!parsed.errors.empty()) {
      for (const std::string& issue : parsed.errors)
        failures.push_back(issue);
      return false;  // Never publish a partial or coerced series.
    }
    std::set<std::string> sessions;
    for (const Json& row : candles->array())
      if (row.type() == Json::Type::kArray && !row.array().empty())
        sessions.insert(row.array()[0].as_string().substr(0, 10));
    const std::string path = config_.history_dir + "/" + symbol + ".csv";
    std::string publish_error;
    if (!PublishHistoryFile(path, parsed.csv, &publish_error)) {
      failures.push_back(symbol + ": " + publish_error);
      return false;
    }
    observed_sessions.insert(sessions.begin(), sessions.end());
    raw_digests[symbol] = Sha256Hex(response.body);
    return true;
  };

  for (const Instrument& instrument : targets) {
    if (processSeries(instrument.symbol, instrument.provider_key))
      ++fetched;
  }
  for (const std::string& symbol : config_.index_symbols) {
    const std::string key = IndexKey(symbol);
    if (key.empty())
      continue;
    if (processSeries(symbol, key))
      ++fetched;
  }
  contract.sessions.assign(observed_sessions.begin(), observed_sessions.end());
  contract.first_date =
      contract.sessions.empty() ? "" : contract.sessions.front();
  contract.last_date =
      contract.sessions.empty() ? "" : contract.sessions.back();
  if (contract.sessions.empty()) {
    if (error)
      *error = "no sessions observed; snapshot contract not publishable";
    sync.status = HistorySyncStatus::kFailed;
    return sync;
  }
  std::string contract_error;
  if (!WriteDatasetContract(config_.history_dir, contract, &contract_error)) {
    if (error)
      *error = "dataset contract not published: " + contract_error;
    sync.status = HistorySyncStatus::kFailed;
    return sync;
  }
  Json::Object files;
  for (const std::string& symbol : contract.symbols) {
    const std::string path = config_.history_dir + "/" + symbol + ".csv";
    std::ifstream input(path, std::ios::binary);
    if (!input.is_open())
      continue;  // Missing required instruments leave the dataset PARTIAL.
    const std::string bytes((std::istreambuf_iterator<char>(input)),
                            std::istreambuf_iterator<char>());
    std::vector<Candle> candles;
    std::string csv_error;
    if (!ReadCsvCandles(path, &candles, &csv_error))
      continue;
    Json::Object entry;
    entry["digest"] = Json::String(Sha256Hex(bytes));
    entry["rows"] = Json::UInteger(static_cast<uint64_t>(candles.size()));
    entry["first_date"] =
        Json::String(candles.empty() ? "" : candles.front().date);
    entry["last_date"] =
        Json::String(candles.empty() ? "" : candles.back().date);
    entry["instrument_id"] = Json::String(contract.instrument_ids.at(symbol));
    entry["raw_digest"] = Json::String(raw_digests[symbol]);
    files[symbol] = Json::ObjectValue(entry);
  }
  Json::Object manifest;
  manifest["schema_version"] = Json::UInteger(2);
  manifest["digest_algorithm"] = Json::String("sha256");
  manifest["contract_digest"] = Json::String(DatasetContractDigest(contract));
  std::ifstream contract_input(config_.history_dir + "/contract.json",
                               std::ios::binary);
  const std::string contract_bytes(
      (std::istreambuf_iterator<char>(contract_input)),
      std::istreambuf_iterator<char>());
  manifest["contract_sha256"] = Json::String(Sha256Hex(contract_bytes));
  manifest["expectations"] =
      Json::ObjectValue(DatasetExpectationsJson(contract));
  manifest["files"] = Json::ObjectValue(files);
  std::string manifest_error;
  if (!PublishHistoryFile(config_.history_dir + "/manifest.json",
                          Json::ObjectValue(manifest).Dump(),
                          &manifest_error)) {
    if (error)
      *error = "history manifest not published: " + manifest_error;
    sync.status = HistorySyncStatus::kFailed;
    return sync;
  }

  sync.expected = contract.symbols.size();
  sync.fetched = fetched;
  sync.failures = failures;
  sync.failed = sync.expected > fetched ? sync.expected - fetched : 0;
  sync.manifest_digest = DatasetContractDigest(contract);
  if (error) {
    if (sync.status == HistorySyncStatus::kComplete)
      error->clear();
    else
      *error = "fetched=" + std::to_string(fetched) +
               " failed=" + std::to_string(sync.failed);
  }
  return sync;
}

bool MarketDataService::FetchQuotes(const std::vector<Instrument>& instruments,
                                    std::vector<Quote>* quotes,
                                    std::string* error) const {
  quotes->clear();
  const Secret token = ResolveSecret(config_, "UPSTOX_ANALYTICS_TOKEN");
  if (token.value.empty()) {
    if (error)
      *error = "Upstox analytics token absent";
    return false;
  }
  const HttpClient http(config_.http_timeout_seconds);
  for (size_t offset = 0; offset < instruments.size(); offset += 400) {
    std::string keys;
    for (size_t i = offset; i < std::min(offset + 400, instruments.size());
         ++i) {
      if (!keys.empty())
        keys += ",";
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
      if (error)
        *error = "quote request failed: " + response.error;
      return false;
    }
    const Json* data = root.Find("data");
    if (data == nullptr || data->type() != Json::Type::kObject)
      continue;
    for (const auto& [key, item] : data->object()) {
      std::string symbol = key;
      const size_t colon = symbol.find(':');
      if (colon != std::string::npos)
        symbol = symbol.substr(colon + 1);
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
    if (error)
      *error = "Upstox analytics token absent";
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
    if (error)
      *error = "Upstox analytics token absent";
    return false;
  }
  const HttpClient http(config_.http_timeout_seconds);
  Json global;
  if (!DownloadGzipJson(
          http, std::string(kAssetsBase) + "/global.json.gz", &global, error)) {
    return false;
  }
  std::vector<std::string> keys;
  if (global.type() == Json::Type::kArray) {
    for (const Json& item : global.array()) {
      const Json* key = item.Find("instrument_key");
      if (key == nullptr)
        continue;
      const std::string value = key->as_string();
      if (value.find("GIFT") != std::string::npos ||
          value.find("SGX") != std::string::npos) {
        keys.push_back(value);
      }
    }
  }
  if (keys.empty())
    return true;
  std::string joined;
  for (const std::string& key : keys) {
    if (!joined.empty())
      joined += ",";
    joined += UrlEncode(key);
  }
  const HttpResponse response =
      http.Get(std::string(kUpstoxBase) +
                   "/market-quote/quotes?instrument_key=" + joined,
               {{"Authorization", "Bearer " + token.value},
                {"Accept", "application/json"}});
  Json root;
  if (!Json::Parse(response.body, &root, error))
    return false;
  const Json* data = root.Find("data");
  if (data == nullptr)
    return false;
  for (const auto& [key, item] : data->object()) {
    quotes->push_back(NormalizeUpstoxQuote(item, "GIFT NIFTY", key));
  }
  return true;
}

}  // namespace quant
