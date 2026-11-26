#include "quant.hpp"

#include <fcntl.h>
#include <unistd.h>

#include <algorithm>
#include <array>
#include <cctype>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iterator>
#include <sstream>
#include <string>
#include <string_view>
#include <system_error>

namespace quant {
namespace {

constexpr int kSchemaVersion = 2;
constexpr const char* kContractFile = "contract.json";
constexpr const char* kManifestFile = "manifest.json";

bool LowerHex(std::string_view value) {
  if (value.size() != 64)
    return false;
  for (char c : value)
    if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f')))
      return false;
  return true;
}

std::string Sha256(std::string_view bytes);

std::string StorageKeyForIdentity(const std::string& instrument_id) {
  return "s" +
         Sha256("QUANT-DATASET-STORAGE-V1\n" + instrument_id).substr(0, 31);
}

bool ValidStorageKey(std::string_view key) {
  if (key.size() != 32 || key.front() != 's')
    return false;
  for (size_t i = 1; i < key.size(); ++i) {
    const char c = key[i];
    if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f')))
      return false;
  }
  return true;
}

std::string BytesToHex(const std::array<uint8_t, 32>& digest) {
  static constexpr char kHex[] = "0123456789abcdef";
  std::string out;
  out.reserve(64);
  for (uint8_t byte : digest) {
    out.push_back(kHex[byte >> 4]);
    out.push_back(kHex[byte & 0x0f]);
  }
  return out;
}

// Compact SHA-256 over exact bytes. This is the single digest algorithm of
// the v2 dataset contract; the FNV hash in engine.cpp remains a non-
// authenticating journal checksum and is never used for dataset manifests.
std::string Sha256(std::string_view bytes) {
  static constexpr std::array<uint32_t, 64> kConstants = {
      0x428a2f98U, 0x71374491U, 0xb5c0fbcfU, 0xe9b5dba5U, 0x3956c25bU,
      0x59f111f1U, 0x923f82a4U, 0xab1c5ed5U, 0xd807aa98U, 0x12835b01U,
      0x243185beU, 0x550c7dc3U, 0x72be5d74U, 0x80deb1feU, 0x9bdc06a7U,
      0xc19bf174U, 0xe49b69c1U, 0xefbe4786U, 0x0fc19dc6U, 0x240ca1ccU,
      0x2de92c6fU, 0x4a7484aaU, 0x5cb0a9dcU, 0x76f988daU, 0x983e5152U,
      0xa831c66dU, 0xb00327c8U, 0xbf597fc7U, 0xc6e00bf3U, 0xd5a79147U,
      0x06ca6351U, 0x14292967U, 0x27b70a85U, 0x2e1b2138U, 0x4d2c6dfcU,
      0x53380d13U, 0x650a7354U, 0x766a0abbU, 0x81c2c92eU, 0x92722c85U,
      0xa2bfe8a1U, 0xa81a664bU, 0xc24b8b70U, 0xc76c51a3U, 0xd192e819U,
      0xd6990624U, 0xf40e3585U, 0x106aa070U, 0x19a4c116U, 0x1e376c08U,
      0x2748774cU, 0x34b0bcb5U, 0x391c0cb3U, 0x4ed8aa4aU, 0x5b9cca4fU,
      0x682e6ff3U, 0x748f82eeU, 0x78a5636fU, 0x84c87814U, 0x8cc70208U,
      0x90befffaU, 0xa4506cebU, 0xbef9a3f7U, 0xc67178f2U};
  std::array<uint32_t, 8> state = {0x6a09e667U,
                                   0xbb67ae85U,
                                   0x3c6ef372U,
                                   0xa54ff53aU,
                                   0x510e527fU,
                                   0x9b05688cU,
                                   0x1f83d9abU,
                                   0x5be0cd19U};
  std::string padded(bytes);
  const uint64_t bit_length = static_cast<uint64_t>(bytes.size()) * 8U;
  padded.push_back(static_cast<char>(0x80));
  while (padded.size() % 64 != 56)
    padded.push_back('\0');
  for (int shift = 56; shift >= 0; shift -= 8)
    padded.push_back(static_cast<char>((bit_length >> shift) & 0xffU));
  auto rotate = [](uint32_t value, uint32_t bits) {
    return (value >> bits) | (value << (32U - bits));
  };
  for (size_t offset = 0; offset < padded.size(); offset += 64) {
    std::array<uint32_t, 64> words{};
    for (size_t i = 0; i < 16; ++i) {
      const size_t at = offset + 4 * i;
      words[i] =
          (static_cast<uint32_t>(static_cast<uint8_t>(padded[at])) << 24) |
          (static_cast<uint32_t>(static_cast<uint8_t>(padded[at + 1])) << 16) |
          (static_cast<uint32_t>(static_cast<uint8_t>(padded[at + 2])) << 8) |
          static_cast<uint32_t>(static_cast<uint8_t>(padded[at + 3]));
    }
    for (size_t i = 16; i < 64; ++i) {
      const uint32_t s0 = rotate(words[i - 15], 7) ^ rotate(words[i - 15], 18) ^
                          (words[i - 15] >> 3);
      const uint32_t s1 = rotate(words[i - 2], 17) ^ rotate(words[i - 2], 19) ^
                          (words[i - 2] >> 10);
      words[i] = words[i - 16] + s0 + words[i - 7] + s1;
    }
    uint32_t a = state[0];
    uint32_t b = state[1];
    uint32_t c = state[2];
    uint32_t d = state[3];
    uint32_t e = state[4];
    uint32_t f = state[5];
    uint32_t g = state[6];
    uint32_t h = state[7];
    for (size_t i = 0; i < 64; ++i) {
      const uint32_t sum1 = rotate(e, 6) ^ rotate(e, 11) ^ rotate(e, 25);
      const uint32_t choice = (e & f) ^ (~e & g);
      const uint32_t temp1 = h + sum1 + choice + kConstants[i] + words[i];
      const uint32_t sum0 = rotate(a, 2) ^ rotate(a, 13) ^ rotate(a, 22);
      const uint32_t majority = (a & b) ^ (a & c) ^ (b & c);
      const uint32_t temp2 = sum0 + majority;
      h = g;
      g = f;
      f = e;
      e = d + temp1;
      d = c;
      c = b;
      b = a;
      a = temp1 + temp2;
    }
    state[0] += a;
    state[1] += b;
    state[2] += c;
    state[3] += d;
    state[4] += e;
    state[5] += f;
    state[6] += g;
    state[7] += h;
  }
  std::array<uint8_t, 32> digest{};
  for (size_t i = 0; i < 8; ++i) {
    digest[4 * i] = static_cast<uint8_t>(state[i] >> 24);
    digest[4 * i + 1] = static_cast<uint8_t>(state[i] >> 16);
    digest[4 * i + 2] = static_cast<uint8_t>(state[i] >> 8);
    digest[4 * i + 3] = static_cast<uint8_t>(state[i]);
  }
  return BytesToHex(digest);
}

bool ValidDate(std::string_view text) {
  if (text.size() != 10 || text[4] != '-' || text[7] != '-')
    return false;
  for (size_t i = 0; i < text.size(); ++i)
    if (i != 4 && i != 7 && !std::isdigit(static_cast<unsigned char>(text[i])))
      return false;
  const int year = std::stoi(std::string(text.substr(0, 4)));
  const int month = std::stoi(std::string(text.substr(5, 2)));
  const int day = std::stoi(std::string(text.substr(8, 2)));
  if (month < 1 || month > 12 || day < 1)
    return false;
  static constexpr int kDays[] = {
      31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
  const bool leap = (year % 4 == 0 && year % 100 != 0) || year % 400 == 0;
  const int limit = kDays[month - 1] + (month == 2 && leap ? 1 : 0);
  return day <= limit;
}

bool SafeToken(std::string_view text) {
  if (text.empty())
    return false;
  for (char c : text) {
    const unsigned char value = static_cast<unsigned char>(c);
    if (!(std::isalnum(value) || c == '.' || c == '_' || c == ':' || c == '-'))
      return false;
  }
  return true;
}

bool SafeSymbol(std::string_view text) {
  if (text.empty())
    return false;
  for (char c : text) {
    if (!(std::isdigit(static_cast<unsigned char>(c)) ||
          std::isupper(static_cast<unsigned char>(c)) || c == ' ' || c == '.' ||
          c == '&' || c == '/' || c == '_' || c == '-'))
      return false;
  }
  return true;
}

std::string ReadFile(const std::string& path, std::string* error) {
  std::ifstream input(path, std::ios::binary);
  if (!input.is_open()) {
    if (error)
      *error = "cannot open " + path;
    return "";
  }
  return std::string((std::istreambuf_iterator<char>(input)),
                     std::istreambuf_iterator<char>());
}

bool WriteFile(const std::string& path,
               const std::string& content,
               std::string* error) {
  std::ofstream output(path, std::ios::binary | std::ios::trunc);
  if (!output.is_open()) {
    if (error)
      *error = "cannot write " + path;
    return false;
  }
  output << content;
  output.flush();
  if (!output.good()) {
    if (error)
      *error = "failed to write " + path;
    return false;
  }
  return true;
}

Json::Object ExpectationsJson(const DatasetExpectations& expected) {
  std::vector<Json> symbols;
  for (const std::string& symbol : expected.symbols)
    symbols.push_back(Json::String(symbol));
  std::vector<Json> sessions;
  for (const std::string& session : expected.sessions)
    sessions.push_back(Json::String(session));
  Json::Object ids;
  for (const auto& [symbol, id] : expected.instrument_ids)
    ids[symbol] = Json::String(id);
  Json::Object result;
  result["schema_version"] = Json::UInteger(kSchemaVersion);
  result["snapshot_id"] = Json::String(expected.snapshot_id);
  result["calendar_id"] = Json::String(expected.calendar_id);
  result["generated_at_ms"] = Json::Integer(expected.generated_at_ms);
  result["provenance"] = Json::String(expected.provenance);
  result["symbols"] = Json::Array(symbols);
  result["sessions"] = Json::Array(sessions);
  result["first_date"] = Json::String(expected.first_date);
  result["last_date"] = Json::String(expected.last_date);
  result["min_rows"] = Json::UInteger(static_cast<uint64_t>(expected.min_rows));
  result["instrument_ids"] = Json::ObjectValue(ids);
  Json::Object keys;
  for (const auto& [symbol, key] : expected.storage_keys)
    keys[symbol] = Json::String(key);
  result["storage_keys"] = Json::ObjectValue(keys);
  return result;
}

bool ParseExpectations(const Json& value,
                       DatasetExpectations* expected,
                       std::string* error) {
  static const std::set<std::string> fields = {"schema_version",
                                               "snapshot_id",
                                               "calendar_id",
                                               "generated_at_ms",
                                               "provenance",
                                               "symbols",
                                               "sessions",
                                               "first_date",
                                               "last_date",
                                               "min_rows",
                                               "instrument_ids",
                                               "storage_keys"};
  if (value.type() != Json::Type::kObject) {
    if (error)
      *error = "expectations must be an object";
    return false;
  }
  for (const auto& [key, ignored] : value.object()) {
    (void)ignored;
    if (fields.find(key) == fields.end()) {
      if (error)
        *error = "unknown expectations field: " + key;
      return false;
    }
  }
  const Json* schema = value.Find("schema_version");
  if (schema == nullptr || !schema->is_exact_integer() ||
      schema->as_int(0) != kSchemaVersion) {
    if (error)
      *error = "unsupported expectations schema";
    return false;
  }
  DatasetExpectations parsed;
  const Json* snapshot = value.Find("snapshot_id");
  const Json* calendar = value.Find("calendar_id");
  const Json* generated = value.Find("generated_at_ms");
  const Json* provenance = value.Find("provenance");
  if (snapshot == nullptr || calendar == nullptr || generated == nullptr ||
      provenance == nullptr || !SafeToken(snapshot->as_string()) ||
      !SafeToken(calendar->as_string()) ||
      !SafeToken(provenance->as_string()) || !generated->is_exact_integer() ||
      generated->as_int(0) < 0) {
    if (error)
      *error = "invalid snapshot identity, provenance or ingestion time";
    return false;
  }
  parsed.snapshot_id = snapshot->as_string();
  parsed.calendar_id = calendar->as_string();
  parsed.generated_at_ms = generated->as_int(0);
  parsed.provenance = provenance->as_string();
  const Json* symbols = value.Find("symbols");
  const Json* sessions = value.Find("sessions");
  if (symbols == nullptr || sessions == nullptr ||
      symbols->type() != Json::Type::kArray ||
      sessions->type() != Json::Type::kArray || symbols->array().empty() ||
      sessions->array().empty()) {
    if (error)
      *error = "symbols and sessions must be non-empty arrays";
    return false;
  }
  for (const Json& item : symbols->array()) {
    if (item.type() != Json::Type::kString || !SafeSymbol(item.as_string())) {
      if (error)
        *error = "invalid symbol grammar";
      return false;
    }
    parsed.symbols.push_back(item.as_string());
  }
  for (const Json& item : sessions->array()) {
    if (item.type() != Json::Type::kString || !ValidDate(item.as_string())) {
      if (error)
        *error = "invalid session date";
      return false;
    }
    parsed.sessions.push_back(item.as_string());
  }
  if (!std::is_sorted(parsed.symbols.begin(), parsed.symbols.end()) ||
      !std::is_sorted(parsed.sessions.begin(), parsed.sessions.end()) ||
      std::adjacent_find(parsed.symbols.begin(), parsed.symbols.end()) !=
          parsed.symbols.end() ||
      std::adjacent_find(parsed.sessions.begin(), parsed.sessions.end()) !=
          parsed.sessions.end()) {
    if (error)
      *error = "symbols and sessions must be sorted and unique";
    return false;
  }
  const Json* first = value.Find("first_date");
  const Json* last = value.Find("last_date");
  const Json* rows = value.Find("min_rows");
  if (first == nullptr || last == nullptr || rows == nullptr ||
      !ValidDate(first->as_string()) || !ValidDate(last->as_string()) ||
      !rows->is_exact_integer() || rows->as_int(0) < 1 ||
      first->as_string() > last->as_string()) {
    if (error)
      *error = "invalid date range or minimum history";
    return false;
  }
  parsed.first_date = first->as_string();
  parsed.last_date = last->as_string();
  parsed.min_rows = rows->as_int(0);
  const Json* ids = value.Find("instrument_ids");
  if (ids == nullptr || ids->type() != Json::Type::kObject ||
      ids->object().size() != parsed.symbols.size()) {
    if (error)
      *error = "instrument identities must cover exactly the symbols";
    return false;
  }
  for (const std::string& symbol : parsed.symbols) {
    const Json* id = ids->Find(symbol);
    if (id == nullptr || id->type() != Json::Type::kString ||
        !SafeToken(id->as_string())) {
      if (error)
        *error = "invalid instrument identity";
      return false;
    }
    parsed.instrument_ids[symbol] = id->as_string();
  }
  const Json* keys = value.Find("storage_keys");
  if (keys == nullptr || keys->type() != Json::Type::kObject ||
      keys->object().size() != parsed.symbols.size()) {
    if (error)
      *error = "storage keys must cover exactly the symbols";
    return false;
  }
  for (const std::string& symbol : parsed.symbols) {
    const Json* key = keys->Find(symbol);
    const auto identity = parsed.instrument_ids.find(symbol);
    if (key == nullptr || key->type() != Json::Type::kString ||
        identity == parsed.instrument_ids.end() ||
        !ValidStorageKey(key->as_string()) ||
        key->as_string() != StorageKeyForIdentity(identity->second)) {
      if (error)
        *error = "storage key differs from permanent identity: " + symbol;
      return false;
    }
    parsed.storage_keys[symbol] = key->as_string();
  }
  if (!ValidateDatasetExpectations(parsed, error))
    return false;
  *expected = parsed;
  return true;
}

void AppendLine(std::string* out, const std::string& line) {
  *out += line;
  out->push_back('\n');
}

}  // namespace

std::string Sha256Hex(std::string_view bytes) {
  return Sha256(bytes);
}

Json::Object DatasetExpectationsJson(const DatasetExpectations& expected) {
  return ExpectationsJson(expected);
}

std::string CanonicalDatasetExpectations(const DatasetExpectations& expected) {
  std::string out;
  AppendLine(&out, "QUANT-DATASET-CONTRACT-V2");
  AppendLine(&out, "schema_version=2");
  AppendLine(&out, "digest_algorithm=sha256");
  AppendLine(&out, "snapshot_id=" + expected.snapshot_id);
  AppendLine(&out, "calendar_id=" + expected.calendar_id);
  AppendLine(&out,
             "generated_at_ms=" + std::to_string(expected.generated_at_ms));
  AppendLine(&out, "provenance=" + expected.provenance);
  std::string symbols;
  for (size_t i = 0; i < expected.symbols.size(); ++i) {
    if (i > 0)
      symbols += ",";
    symbols += expected.symbols[i];
  }
  AppendLine(&out, "symbols=" + symbols);
  std::string sessions;
  for (size_t i = 0; i < expected.sessions.size(); ++i) {
    if (i > 0)
      sessions += ",";
    sessions += expected.sessions[i];
  }
  AppendLine(&out, "sessions=" + sessions);
  AppendLine(&out, "first_date=" + expected.first_date);
  AppendLine(&out, "last_date=" + expected.last_date);
  AppendLine(&out, "min_rows=" + std::to_string(expected.min_rows));
  std::string identities;
  for (size_t i = 0; i < expected.symbols.size(); ++i) {
    if (i > 0)
      identities += ",";
    const auto identity = expected.instrument_ids.find(expected.symbols[i]);
    identities +=
        expected.symbols[i] + ":" +
        (identity == expected.instrument_ids.end() ? std::string()
                                                   : identity->second);
  }
  AppendLine(&out, "instrument_ids=" + identities);
  std::string keys;
  for (size_t i = 0; i < expected.symbols.size(); ++i) {
    if (i > 0)
      keys += ",";
    const auto key = expected.storage_keys.find(expected.symbols[i]);
    keys += expected.symbols[i] + ":" +
            (key == expected.storage_keys.end() ? std::string() : key->second);
  }
  AppendLine(&out, "storage_keys=" + keys);
  return out;
}

std::string DatasetContractDigest(const DatasetExpectations& expected) {
  return Sha256Hex(CanonicalDatasetExpectations(expected));
}

std::string DatasetStorageKey(const std::string& instrument_id) {
  return StorageKeyForIdentity(instrument_id);
}

bool ValidateDatasetExpectations(const DatasetExpectations& expected,
                                 std::string* error) {
  DatasetExpectations copy = expected;
  std::sort(copy.symbols.begin(), copy.symbols.end());
  std::sort(copy.sessions.begin(), copy.sessions.end());
  if (copy.symbols.empty() || copy.sessions.empty() ||
      std::adjacent_find(copy.symbols.begin(), copy.symbols.end()) !=
          copy.symbols.end() ||
      std::adjacent_find(copy.sessions.begin(), copy.sessions.end()) !=
          copy.sessions.end()) {
    if (error)
      *error = "symbols and sessions must be non-empty and unique";
    return false;
  }
  if (!SafeToken(copy.snapshot_id) || !SafeToken(copy.calendar_id) ||
      !SafeToken(copy.provenance) || copy.generated_at_ms < 0 ||
      !ValidDate(copy.first_date) || !ValidDate(copy.last_date) ||
      copy.first_date > copy.last_date || copy.min_rows < 1 ||
      copy.sessions.empty() || copy.first_date != copy.sessions.front() ||
      copy.last_date != copy.sessions.back()) {
    if (error)
      *error = "invalid snapshot identity, provenance, range or history";
    return false;
  }
  for (const std::string& symbol : copy.symbols) {
    const auto identity = copy.instrument_ids.find(symbol);
    const auto key = copy.storage_keys.find(symbol);
    if (!SafeSymbol(symbol) || identity == copy.instrument_ids.end() ||
        key == copy.storage_keys.end() || !SafeToken(identity->second) ||
        key->second != StorageKeyForIdentity(identity->second)) {
      if (error)
        *error = "invalid symbol or instrument identity";
      return false;
    }
  }
  return true;
}

bool ParseDatasetExpectations(const Json& value,
                              DatasetExpectations* expected,
                              std::string* error) {
  return ParseExpectations(value, expected, error);
}

bool ReadDatasetExpectations(const std::string& history_dir,
                             DatasetExpectations* expected,
                             std::string* error) {
  if (expected == nullptr) {
    if (error)
      *error = "expectations output required";
    return false;
  }
  std::string parse_error;
  const std::string content =
      ReadFile(history_dir + "/" + kContractFile, &parse_error);
  if (!parse_error.empty()) {
    if (error)
      *error = parse_error;
    return false;
  }
  Json parsed;
  if (!Json::Parse(content, &parsed, error))
    return false;
  return ParseExpectations(parsed, expected, error);
}

bool WriteDatasetContract(const std::string& history_dir,
                          const DatasetExpectations& expected,
                          std::string* error) {
  DatasetExpectations sorted = expected;
  std::sort(sorted.symbols.begin(), sorted.symbols.end());
  std::sort(sorted.sessions.begin(), sorted.sessions.end());
  if (!ValidateDatasetExpectations(sorted, error))
    return false;
  std::error_code ec;
  std::filesystem::create_directories(history_dir, ec);
  if (ec) {
    if (error)
      *error = "cannot create dataset directory";
    return false;
  }
  return WriteFile(history_dir + "/" + kContractFile,
                   Json::ObjectValue(ExpectationsJson(sorted)).Dump() + "\n",
                   error);
}

bool WriteDatasetManifest(const std::string& history_dir,
                          const DatasetExpectations& expected,
                          std::string* error) {
  if (!WriteDatasetContract(history_dir, expected, error))
    return false;
  Json::Object files;
  for (const std::string& symbol : expected.symbols) {
    const std::string path =
        history_dir + "/" + expected.storage_keys.at(symbol) + ".csv";
    std::string read_error;
    const std::string bytes = ReadFile(path, &read_error);
    if (!read_error.empty()) {
      if (error)
        *error = read_error;
      return false;
    }
    std::vector<Candle> candles;
    if (!ReadCsvCandles(path, &candles, error))
      return false;
    std::vector<std::string> sessions;
    for (const Candle& candle : candles)
      sessions.push_back(candle.date);
    if (sessions != expected.sessions ||
        candles.size() < static_cast<size_t>(expected.min_rows)) {
      if (error)
        *error = "series does not satisfy the snapshot contract: " + symbol;
      return false;
    }
    Json::Object entry;
    entry["digest"] = Json::String(Sha256Hex(bytes));
    entry["rows"] = Json::UInteger(static_cast<uint64_t>(candles.size()));
    entry["first_date"] = Json::String(sessions.front());
    entry["last_date"] = Json::String(sessions.back());
    entry["instrument_id"] = Json::String(expected.instrument_ids.at(symbol));
    entry["storage_key"] = Json::String(expected.storage_keys.at(symbol));
    entry["raw_digest"] = Json::String("");
    files[symbol] = Json::ObjectValue(entry);
  }
  Json::Object manifest;
  manifest["schema_version"] = Json::UInteger(kSchemaVersion);
  manifest["digest_algorithm"] = Json::String("sha256");
  manifest["contract_digest"] = Json::String(DatasetContractDigest(expected));
  std::string contract_error;
  const std::string contract_bytes =
      ReadFile(history_dir + "/" + kContractFile, &contract_error);
  if (!contract_error.empty()) {
    if (error)
      *error = contract_error;
    return false;
  }
  manifest["contract_sha256"] = Json::String(Sha256Hex(contract_bytes));
  manifest["expectations"] = Json::ObjectValue(ExpectationsJson(expected));
  manifest["files"] = Json::ObjectValue(files);
  return WriteFile(history_dir + "/" + kManifestFile,
                   Json::ObjectValue(manifest).Dump() + "\n",
                   error);
}

DatasetVerification VerifyDataset(const std::string& history_dir,
                                  std::string* error) {
  (void)history_dir;
  DatasetVerification result;
  result.status = DatasetStatus::kBlocked;
  result.errors.push_back("authoritative dataset expectations required");
  if (error)
    *error = result.errors.front();
  return result;
}

DatasetVerification VerifyDataset(const std::string& history_dir,
                                  const DatasetExpectations* expected,
                                  std::string* error) {
  DatasetVerification result;
  if (expected == nullptr)
    return VerifyDataset(history_dir, error);
  auto block = [&result, error](const std::string& reason) {
    result.status = DatasetStatus::kBlocked;
    result.errors.push_back(reason);
    if (error)
      *error = reason;
    return result;
  };
  DatasetExpectations contract;
  std::string parse_error;
  if (!ReadDatasetExpectations(history_dir, &contract, &parse_error))
    return block("snapshot contract invalid: " + parse_error);
  if (contract.snapshot_id != expected->snapshot_id ||
      contract.calendar_id != expected->calendar_id ||
      contract.generated_at_ms != expected->generated_at_ms ||
      contract.provenance != expected->provenance ||
      contract.symbols != expected->symbols ||
      contract.sessions != expected->sessions ||
      contract.first_date != expected->first_date ||
      contract.last_date != expected->last_date ||
      contract.min_rows != expected->min_rows ||
      contract.instrument_ids != expected->instrument_ids)
    return block("snapshot contract differs from authoritative expectations");
  const std::string manifest_path = history_dir + "/" + kManifestFile;
  std::ifstream manifest_input(manifest_path);
  if (!manifest_input.is_open())
    return block("dataset manifest missing");
  const std::string manifest_content(
      (std::istreambuf_iterator<char>(manifest_input)),
      std::istreambuf_iterator<char>());
  Json manifest;
  if (!Json::Parse(manifest_content, &manifest, &parse_error))
    return block("dataset manifest invalid: " + parse_error);
  static const std::set<std::string> manifest_fields = {"schema_version",
                                                        "digest_algorithm",
                                                        "contract_digest",
                                                        "contract_sha256",
                                                        "expectations",
                                                        "files"};
  for (const auto& [key, ignored] : manifest.object()) {
    (void)ignored;
    if (manifest_fields.find(key) == manifest_fields.end())
      return block("unknown manifest field: " + key);
  }
  const Json* schema = manifest.Find("schema_version");
  const Json* algorithm = manifest.Find("digest_algorithm");
  if (schema == nullptr || !schema->is_exact_integer() ||
      schema->as_int(0) != kSchemaVersion)
    return block("unsupported dataset schema");
  if (algorithm == nullptr || algorithm->as_string() != "sha256")
    return block("unsupported dataset digest algorithm");
  const Json* digest = manifest.Find("contract_digest");
  const Json* contract_hash = manifest.Find("contract_sha256");
  if (digest == nullptr || contract_hash == nullptr ||
      !LowerHex(digest->as_string()) || !LowerHex(contract_hash->as_string()))
    return block("malformed dataset digests");
  DatasetExpectations manifest_expectations;
  const Json* expectations = manifest.Find("expectations");
  if (expectations == nullptr ||
      !ParseExpectations(*expectations, &manifest_expectations, &parse_error))
    return block("manifest expectations invalid: " + parse_error);
  const std::string canonical = CanonicalDatasetExpectations(contract);
  if (manifest_expectations.snapshot_id != contract.snapshot_id ||
      manifest_expectations.calendar_id != contract.calendar_id ||
      manifest_expectations.generated_at_ms != contract.generated_at_ms ||
      manifest_expectations.provenance != contract.provenance ||
      manifest_expectations.symbols != contract.symbols ||
      manifest_expectations.sessions != contract.sessions ||
      manifest_expectations.first_date != contract.first_date ||
      manifest_expectations.last_date != contract.last_date ||
      manifest_expectations.min_rows != contract.min_rows ||
      manifest_expectations.instrument_ids != contract.instrument_ids ||
      manifest_expectations.storage_keys != contract.storage_keys)
    return block("manifest expectations differ from snapshot contract");
  if (digest->as_string() != Sha256Hex(canonical))
    return block("contract digest mismatch");
  const std::string contract_bytes =
      ReadFile(history_dir + "/" + kContractFile, &parse_error);
  if (!parse_error.empty() ||
      contract_hash->as_string() != Sha256Hex(contract_bytes))
    return block("contract byte digest mismatch");
  const Json* files = manifest.Find("files");
  if (files == nullptr || files->type() != Json::Type::kObject)
    return block("manifest files must be an object");
  if (files->object().size() != contract.symbols.size())
    return block("manifest files differ from expected instruments");
  for (const std::string& symbol : contract.symbols) {
    const auto file = files->object().find(symbol);
    if (file == files->object().end()) {
      result.errors.push_back("missing dataset file: " + symbol);
      continue;
    }
    const Json& entry = file->second;
    if (entry.type() != Json::Type::kObject || entry.object().size() != 7) {
      result.errors.push_back("malformed file identity: " + symbol);
      continue;
    }
    static const std::set<std::string> file_fields = {"digest",
                                                      "rows",
                                                      "first_date",
                                                      "last_date",
                                                      "instrument_id",
                                                      "storage_key",
                                                      "raw_digest"};
    bool well_formed = true;
    for (const auto& [key, ignored] : entry.object()) {
      (void)ignored;
      if (file_fields.find(key) == file_fields.end())
        well_formed = false;
    }
    const Json* file_digest = entry.Find("digest");
    const Json* rows = entry.Find("rows");
    const Json* first = entry.Find("first_date");
    const Json* last = entry.Find("last_date");
    const Json* instrument = entry.Find("instrument_id");
    const Json* storage = entry.Find("storage_key");
    const Json* raw = entry.Find("raw_digest");
    if (!well_formed || file_digest == nullptr || rows == nullptr ||
        first == nullptr || last == nullptr || instrument == nullptr ||
        storage == nullptr || raw == nullptr ||
        !LowerHex(file_digest->as_string()) || !rows->is_exact_integer() ||
        rows->as_int(0) <= 0 || raw->as_string().size() > 64) {
      result.errors.push_back("malformed file identity: " + symbol);
      continue;
    }
    for (char c : raw->as_string())
      if (!std::isxdigit(static_cast<unsigned char>(c))) {
        result.errors.push_back("malformed raw digest: " + symbol);
        well_formed = false;
        break;
      }
    if (!well_formed)
      continue;
    const std::string path =
        history_dir + "/" + contract.storage_keys.at(symbol) + ".csv";
    std::ifstream input(path, std::ios::binary);
    if (!input.is_open()) {
      result.errors.push_back("dataset file missing: " + symbol);
      continue;
    }
    const std::string bytes((std::istreambuf_iterator<char>(input)),
                            std::istreambuf_iterator<char>());
    const std::string actual = Sha256Hex(bytes);
    if (actual != file_digest->as_string()) {
      result.errors.push_back("dataset digest mismatch: " + symbol);
      continue;
    }
    std::vector<Candle> candles;
    std::string csv_error;
    if (!ReadCsvCandles(path, &candles, &csv_error)) {
      result.errors.push_back(csv_error);
      continue;
    }
    std::vector<std::string> sessions;
    for (const Candle& candle : candles)
      sessions.push_back(candle.date);
    if (sessions != contract.sessions) {
      result.errors.push_back("session coverage mismatch: " + symbol);
      continue;
    }
    if (rows->as_int(0) != static_cast<int64_t>(candles.size()) ||
        candles.size() < static_cast<size_t>(contract.min_rows)) {
      result.errors.push_back("dataset row count mismatch: " + symbol);
      continue;
    }
    if (first->as_string() != sessions.front() ||
        last->as_string() != sessions.back() ||
        instrument->as_string() != contract.instrument_ids.at(symbol) ||
        storage->as_string() != contract.storage_keys.at(symbol)) {
      result.errors.push_back("identity or range mismatch: " + symbol);
      continue;
    }
    result.file_digests[symbol] = actual;
  }
  if (result.file_digests.empty())
    result.status = DatasetStatus::kFailed;
  else if (result.errors.empty())
    result.status = DatasetStatus::kComplete;
  else
    result.status = DatasetStatus::kPartial;
  if (error)
    error->clear();
  return result;
}

}  // namespace quant
