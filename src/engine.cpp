#include "quant.hpp"

#include <curl/curl.h>
#include <zlib.h>

#include <cerrno>
#include <charconv>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iterator>
#include <limits>
#include <numeric>
#include <random>
#include <set>
#include <sstream>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>

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
    if (pos >= inner.size())
      break;
    if (inner[pos] != '"')
      return false;
    const size_t quote_end = inner.find('"', pos + 1);
    if (quote_end == std::string::npos)
      return false;
    out->push_back(std::string(inner.substr(pos + 1, quote_end - pos - 1)));
    pos = quote_end + 1;
  }
  return true;
}

std::string UtcSessionDate(int64_t now_ms) {
  const time_t seconds = static_cast<time_t>(now_ms / 1000);
  std::tm utc{};
  gmtime_r(&seconds, &utc);
  std::ostringstream oss;
  oss << std::put_time(&utc, "%Y-%m-%d");
  return oss.str();
}

using money_detail::CheckedAdd;
using money_detail::CheckedMultiply;

bool ParseInteger64(const std::string& text, int64_t* out) {
  if (text.empty())
    return false;
  errno = 0;
  char* end = nullptr;
  const long long value = std::strtoll(text.c_str(), &end, 10);
  if (errno == ERANGE || end != text.c_str() + text.size())
    return false;
  *out = static_cast<int64_t>(value);
  return true;
}

bool ParseUnsigned64(const std::string& text, uint64_t* out) {
  if (text.empty() || text.front() == '-')
    return false;
  errno = 0;
  char* end = nullptr;
  const unsigned long long value = std::strtoull(text.c_str(), &end, 10);
  if (errno == ERANGE || end != text.c_str() + text.size())
    return false;
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
  if (text.empty())
    return false;
  errno = 0;
  char* end = nullptr;
  const double value = std::strtod(text.c_str(), &end);
  if (errno == ERANGE || end != text.c_str() + text.size())
    return false;
  *out = value;
  return true;
}

int64_t IntegerOr(const std::map<std::string, std::string>& values,
                  const std::string& key,
                  int64_t fallback) {
  const auto it = values.find(key);
  if (it == values.end())
    return fallback;
  int64_t value = 0;
  return ParseInteger64(it->second, &value) ? value : fallback;
}

double DoubleOr(const std::map<std::string, std::string>& values,
                const std::string& key,
                double fallback) {
  const auto it = values.find(key);
  if (it == values.end())
    return fallback;
  double value = 0.0;
  return ParseFloating(it->second, &value) ? value : fallback;
}

std::string StringOr(const std::map<std::string, std::string>& values,
                     const std::string& key,
                     const std::string& fallback) {
  const auto it = values.find(key);
  return it == values.end() ? fallback : it->second;
}

bool BoolOr(const std::map<std::string, std::string>& values,
            const std::string& key,
            bool fallback) {
  const auto it = values.find(key);
  if (it == values.end())
    return fallback;
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
    if (c >= '0' && c <= '9')
      return c - '0';
    if (c >= 'a' && c <= 'f')
      return c - 'a' + 10;
    if (c >= 'A' && c <= 'F')
      return c - 'A' + 10;
    return -1;
  };
  std::string out;
  for (size_t i = 0; i + 1 < input.size(); i += 2) {
    const int hi = nibble(input[i]);
    const int lo = nibble(input[i + 1]);
    if (hi < 0 || lo < 0)
      return "";
    out.push_back(static_cast<char>((hi << 4) | lo));
  }
  return out;
}

size_t CurlWrite(void* contents, size_t size, size_t nmemb, void* userdata) {
  const size_t bytes = size * nmemb;
  auto* body = static_cast<std::string*>(userdata);
  body->append(static_cast<const char*>(contents), bytes);
  return bytes;
}

double Mean(const std::vector<double>& values) {
  if (values.empty())
    return 0.0;
  return std::accumulate(values.begin(), values.end(), 0.0) /
         static_cast<double>(values.size());
}

// Canonical volatility convention for QUANT: population standard deviation
// (divide by n). q's dev/mdev and Python's statistics.pstdev use the same
// denominator. Sharpe/Sortino use this same convention so there is exactly
// one definition of dispersion in the platform.
double StdDev(const std::vector<double>& values) {
  if (values.empty())
    return 0.0;
  const double mean = Mean(values);
  double sum = 0.0;
  for (double value : values) {
    const double delta = value - mean;
    sum += delta * delta;
  }
  return std::sqrt(sum / static_cast<double>(values.size()));
}

double Clamp(double value, double low, double high) {
  return std::max(low, std::min(high, value));
}

}  // namespace

std::string ChecksumHex(std::string_view text) {
  std::ostringstream oss;
  oss << std::hex << std::setw(16) << std::setfill('0') << Checksum(text);
  return oss.str();
}

// ---------------------------------------------------------------------------
// Money.
// ---------------------------------------------------------------------------
bool Money::TryFromRupees(double rupees, Money* out) {
  if (out == nullptr || !std::isfinite(rupees))
    return false;
  constexpr double kMaxRupees = 9.0e16;
  if (std::abs(rupees) > kMaxRupees)
    return false;
  const long long paise = std::llround(rupees * 100.0);
  if (paise == std::numeric_limits<long long>::min())
    return false;
  *out = FromPaise(static_cast<int64_t>(paise));
  return true;
}

Money Money::FromRupees(double rupees) {
  Money result;
  if (!TryFromRupees(rupees, &result))
    return Invalid();
  return result;
}

namespace {

bool CheckedMoneyAdd(Money a, Money b, Money* out) {
  if (out == nullptr)
    return false;
  int64_t left = 0;
  int64_t right = 0;
  int64_t sum = 0;
  if (!a.TryPaise(&left) || !b.TryPaise(&right) ||
      !money_detail::CheckedAdd(left, right, &sum))
    return false;
  *out = Money::FromPaise(sum);
  return true;
}

bool CheckedMoneySubtract(Money a, Money b, Money* out) {
  if (out == nullptr)
    return false;
  int64_t left = 0;
  int64_t right = 0;
  int64_t diff = 0;
  if (!a.TryPaise(&left) || !b.TryPaise(&right) ||
      !money_detail::CheckedSubtract(left, right, &diff))
    return false;
  *out = Money::FromPaise(diff);
  return true;
}

bool PositivePaise(Money value, int64_t* out) {
  int64_t raw = 0;
  if (out == nullptr || !value.TryPaise(&raw) || raw <= 0)
    return false;
  *out = raw;
  return true;
}

}  // namespace

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
  if (text == "BACKTEST")
    return Mode::kBacktest;
  if (text == "PAPER")
    return Mode::kPaper;
  if (text == "READ_ONLY_BROKER")
    return Mode::kReadOnlyBroker;
  if (text == "LIVE")
    return Mode::kLive;
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
    if (cleaned.empty())
      continue;
    if (!pending.empty()) {
      pending += "\n" + cleaned;
      if (cleaned.find(']') == std::string::npos)
        continue;
      cleaned = pending;
      pending.clear();
    }
    if (cleaned.front() == '[' && cleaned.back() == ']') {
      section = Trim(cleaned.substr(1, cleaned.size() - 2));
      continue;
    }
    const size_t equals = cleaned.find('=');
    if (equals == std::string::npos)
      continue;
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
  c.max_participation_fraction =
      DoubleOr(values, exe + "MAX_PARTICIPATION_FRACTION", 0.01);
  c.stop_loss_fraction = DoubleOr(values, exe + "STOP_LOSS_FRACTION", 0.05);
  c.backtest_delivery = BoolOr(values, exe + "BACKTEST_DELIVERY", true);
  c.execution_delivery = BoolOr(values, exe + "EXECUTION_DELIVERY", true);
  c.terminal_liquidation = BoolOr(values, exe + "TERMINAL_LIQUIDATION", false);
  c.min_traded_value_inr = DoubleOr(values, exe + "MIN_TRADED_VALUE_INR", 5e6);
  c.slippage_bps = DoubleOr(values, exe + "SLIPPAGE_BPS", 10.0);
  c.queue_capacity =
      static_cast<size_t>(IntegerOr(values, exe + "QUEUE_CAPACITY", 4096));

  auto find_array = [&values](const std::string& key) {
    std::vector<std::string> out;
    const auto it = values.find(key);
    if (it != values.end())
      ParseArray(it->second, &out);
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
  int64_t capital = 0;
  if (!config.initial_capital.TryPaise(&capital) || capital <= 0) {
    errors.push_back("INITIAL_CAPITAL_INR must be a valid positive amount");
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
  if (config.history_days == 0 || config.universe_limit == 0 ||
      config.shortlist_size == 0) {
    errors.push_back("history, universe and shortlist sizes must be positive");
  }
  if (config.shortlist_size > config.universe_limit) {
    errors.push_back("SHORTLIST_SIZE cannot exceed UNIVERSE_LIMIT");
  }
  if (!std::isfinite(config.max_spread_bps) || config.max_spread_bps < 0.0) {
    errors.push_back("MAX_SPREAD_BPS must be finite and non-negative");
  }
  if (!std::isfinite(config.slippage_bps) || config.slippage_bps < 0.0) {
    errors.push_back("SLIPPAGE_BPS must be finite and non-negative");
  }
  if (!std::isfinite(config.stop_loss_fraction) ||
      !(config.stop_loss_fraction > 0.0 && config.stop_loss_fraction <= 0.5)) {
    errors.push_back("STOP_LOSS_FRACTION must be finite and in (0, 0.5]");
  }
  if (!std::isfinite(config.max_participation_fraction) ||
      !(config.max_participation_fraction > 0.0 &&
        config.max_participation_fraction <= 1.0)) {
    errors.push_back("MAX_PARTICIPATION_FRACTION must be finite and in (0, 1]");
  }
  if (!std::isfinite(config.min_traded_value_inr) ||
      config.min_traded_value_inr < 0.0 ||
      config.min_traded_value_inr > 1.0e12) {
    errors.push_back("MIN_TRADED_VALUE_INR must be finite and bounded");
  }
  if (config.queue_capacity == 0 || config.queue_capacity > (1u << 20)) {
    errors.push_back("QUEUE_CAPACITY must be in [1, 1048576]");
  }
  if (config.stale_quote_seconds <= 0 || config.stale_quote_seconds > 86400) {
    errors.push_back("STALE_QUOTE_SECONDS must be in [1, 86400]");
  }
  if (config.http_timeout_seconds <= 0 || config.http_timeout_seconds > 3600) {
    errors.push_back("HTTP_TIMEOUT_SECONDS must be in [1, 3600]");
  }
  if (config.kdbx.timeout_ms <= 0 || config.kdbx.timeout_ms > 3600000) {
    errors.push_back("KDB-X timeout must be in [1, 3600000]");
  }
  if (config.kdbx.batch_size == 0 || config.kdbx.batch_size > (1u << 20)) {
    errors.push_back("KDB-X batch size must be in [1, 1048576]");
  }
  if (config.data_dir.empty() || config.journal_path.empty() ||
      config.report_dir.empty() || config.keychain_prefix.empty()) {
    errors.push_back("storage paths and keychain prefix must be non-empty");
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
const char* CalculationStatusName(CalculationStatus status) {
  switch (status) {
    case CalculationStatus::kCalculated:
      return "CALCULATED";
    case CalculationStatus::kEstimated:
      return "ESTIMATED";
    case CalculationStatus::kUnsupported:
      return "UNSUPPORTED";
    case CalculationStatus::kInvalidInput:
      return "INVALID_INPUT";
    case CalculationStatus::kOverflow:
      return "OVERFLOW";
  }
  return "UNKNOWN";
}

namespace {

// Exact integer-rational scaling of an integer paise amount. All statutory
// rates are expressed as rational numbers; no binary floating point is used
// in their calculation.
bool ScalePaise(int64_t base_paise,
                int64_t numerator,
                int64_t denominator,
                int64_t* out,
                bool round_half_up) {
  if (out == nullptr || denominator <= 0)
    return false;
  int64_t scaled = 0;
  if (!money_detail::CheckedMultiply(base_paise, numerator, &scaled))
    return false;
  if (round_half_up &&
      !money_detail::CheckedAdd(scaled, denominator / 2, &scaled))
    return false;
  *out = scaled / denominator;
  return true;
}

// Zerodha rounds STT to the nearest rupee with 50 paise rounding up.
// Delivery STT is 0.1% (1/1000 of rupees); intraday sell STT is 0.025%
// (1/4000 of rupees). With turnover in paise the divisors become 100000 and
// 400000 respectively, so the whole calculation is exact integer arithmetic.
bool RoundedSttPaise(int64_t turnover_paise, int64_t divisor, int64_t* out) {
  int64_t rupees = 0;
  if (!ScalePaise(turnover_paise, 1, divisor, &rupees, true))
    return false;
  return money_detail::CheckedMultiply(rupees, 100, out);
}

}  // namespace

CostBreakdown EstimateCosts(Money turnover,
                            bool delivery,
                            bool is_buy,
                            double slippage_bps,
                            bool charge_dp) {
  CostBreakdown result;
  if (!turnover.valid() || !std::isfinite(slippage_bps) || slippage_bps < 0.0) {
    result.status = CalculationStatus::kInvalidInput;
    result.brokerage = Money::Invalid();
    result.stt = Money::Invalid();
    result.exchange_fee = Money::Invalid();
    result.gst = Money::Invalid();
    result.sebi = Money::Invalid();
    result.stamp = Money::Invalid();
    result.dp = Money::Invalid();
    result.slippage = Money::Invalid();
    result.total = Money::Invalid();
    return result;
  }
  int64_t value = 0;
  if (!turnover.TryPaise(&value)) {
    result.status = CalculationStatus::kInvalidInput;
    return result;
  }
  if (value < 0) {
    result.status = CalculationStatus::kInvalidInput;
    return result;
  }
  if (value == 0) {
    // A zero-turnover estimate is a legitimate no-trade result.
    result.status = CalculationStatus::kCalculated;
    return result;
  }

  result.status = charge_dp && delivery && !is_buy
                      ? CalculationStatus::kEstimated
                      : CalculationStatus::kCalculated;

  if (delivery) {
    result.brokerage = Money::FromPaise(0);
  } else {
    int64_t brokerage = 0;
    if (!ScalePaise(value, 3, 10000, &brokerage, false)) {
      result.status = CalculationStatus::kOverflow;
      return result;
    }
    result.brokerage = Money::FromPaise(std::min<int64_t>(brokerage, 2000));
  }

  int64_t stt_divisor = 0;
  if (delivery)
    stt_divisor = 100000;
  else if (!is_buy)
    stt_divisor = 400000;
  int64_t stt = 0;
  if (stt_divisor != 0 && !RoundedSttPaise(value, stt_divisor, &stt)) {
    result.status = CalculationStatus::kOverflow;
    return result;
  }
  result.stt = Money::FromPaise(stt);

  int64_t exchange = 0;
  if (!ScalePaise(value, 307, 10000000, &exchange, false)) {
    result.status = CalculationStatus::kOverflow;
    return result;
  }
  result.exchange_fee = Money::FromPaise(exchange);

  int64_t sebi = 0;
  if (!ScalePaise(value, 1, 1000000, &sebi, true)) {
    result.status = CalculationStatus::kOverflow;
    return result;
  }
  result.sebi = Money::FromPaise(sebi);

  int64_t gst_base = 0;
  int64_t gst = 0;
  if (!money_detail::CheckedAdd(
          result.brokerage.paise(), result.exchange_fee.paise(), &gst_base) ||
      !money_detail::CheckedAdd(gst_base, result.sebi.paise(), &gst_base) ||
      !ScalePaise(gst_base, 18, 100, &gst, false)) {
    result.status = CalculationStatus::kOverflow;
    return result;
  }
  result.gst = Money::FromPaise(gst);

  if (is_buy) {
    int64_t stamp = 0;
    if (!ScalePaise(value, delivery ? 15 : 3, 100000, &stamp, false)) {
      result.status = CalculationStatus::kOverflow;
      return result;
    }
    result.stamp = Money::FromPaise(stamp);
  } else {
    result.stamp = Money::FromPaise(0);
  }

  if (charge_dp && delivery && !is_buy)
    result.dp = Money::FromRupees(15.34);  // ESTIMATED: per scrip, per day.
  else
    result.dp = Money::FromPaise(0);

  result.slippage = Money::FromRupees(static_cast<double>(value) / 100.0 *
                                      slippage_bps / 10000.0);

  int64_t total = 0;
  if (!money_detail::CheckedAdd(total, result.brokerage.paise(), &total) ||
      !money_detail::CheckedAdd(total, result.stt.paise(), &total) ||
      !money_detail::CheckedAdd(total, result.exchange_fee.paise(), &total) ||
      !money_detail::CheckedAdd(total, result.gst.paise(), &total) ||
      !money_detail::CheckedAdd(total, result.sebi.paise(), &total) ||
      !money_detail::CheckedAdd(total, result.stamp.paise(), &total) ||
      !money_detail::CheckedAdd(total, result.dp.paise(), &total) ||
      !money_detail::CheckedAdd(total, result.slippage.paise(), &total)) {
    result.status = CalculationStatus::kOverflow;
    return result;
  }
  if (!result.slippage.valid()) {
    result.status = CalculationStatus::kOverflow;
    return result;
  }
  result.total = Money::FromPaise(total);
  return result;
}

CostBreakdown CostAggregator::Estimate(Money turnover,
                                       bool delivery,
                                       bool is_buy,
                                       double slippage_bps,
                                       const std::string& symbol,
                                       const std::string& session) {
  // Estimation is pure. DP entitlement is recognised only by
  // RecognizeExecution when a committed execution is recognized.
  (void)symbol;
  (void)session;
  return EstimateCosts(turnover, delivery, is_buy, slippage_bps, false);
}

bool CostAggregator::RecognizeExecution(const std::string& symbol,
                                        const std::string& session,
                                        bool delivery,
                                        bool is_buy,
                                        bool* dp_applicable,
                                        std::string* error) {
  if (dp_applicable)
    *dp_applicable = false;
  if (symbol.empty() || session.empty()) {
    if (error)
      *error = "execution requires symbol and session identity";
    return false;
  }
  if (!(delivery && !is_buy))
    return true;
  const std::string key = session + "|" + symbol;
  if (dp_charged_.find(key) != dp_charged_.end())
    return true;
  dp_charged_.insert(key);
  if (dp_applicable)
    *dp_applicable = true;
  return true;
}

double BreakEvenMoveBps(Money notional,
                        const CostBreakdown& entry,
                        const CostBreakdown& exit_costs) {
  int64_t n = 0;
  int64_t e = 0;
  int64_t x = 0;
  if (!notional.TryPaise(&n) || n <= 0 || !entry.total.TryPaise(&e) ||
      !exit_costs.total.TryPaise(&x))
    return std::numeric_limits<double>::quiet_NaN();
  int64_t sum = 0;
  if (!money_detail::CheckedAdd(e, x, &sum))
    return std::numeric_limits<double>::quiet_NaN();
  return static_cast<double>(sum) / static_cast<double>(n) * 10000.0;
}

std::string FormatCosts(const CostBreakdown& costs) {
  auto field = [](const char* name, Money value) {
    double amount = 0.0;
    std::ostringstream oss;
    oss << name << "=";
    if (value.TryRupees(&amount))
      oss << amount;
    else
      oss << "UNAVAILABLE";
    return oss.str();
  };
  std::ostringstream oss;
  oss << field("brokerage", costs.brokerage) << " " << field("stt", costs.stt)
      << " " << field("exchange", costs.exchange_fee) << " "
      << field("sebi", costs.sebi) << " " << field("gst", costs.gst) << " "
      << field("stamp", costs.stamp) << " " << field("dp", costs.dp) << " "
      << field("slippage", costs.slippage) << " " << field("total", costs.total)
      << " (" << costs.version << "," << CalculationStatusName(costs.status)
      << ")";
  return oss.str();
}

// ---------------------------------------------------------------------------
// KDB-X checkpoint contract.
// ---------------------------------------------------------------------------
std::string KdbxCheckpointCanonical(const KdbxCheckpoint& checkpoint) {
  std::ostringstream oss;
  oss << checkpoint.version << '|' << checkpoint.journal_watermark << '|'
      << checkpoint.quote_count << '|' << checkpoint.bar_count << '|'
      << checkpoint.feature_count << '|' << (checkpoint.complete ? 1 : 0) << '|'
      << checkpoint.runtime_version << '|' << checkpoint.created_at_ms;
  return oss.str();
}

bool SaveKdbxCheckpoint(const std::string& path,
                        const KdbxCheckpoint& checkpoint,
                        std::string* error) {
  if (path.empty() || checkpoint.version != 1 || !checkpoint.complete) {
    if (error)
      *error = "incomplete or unsupported KDB-X checkpoint";
    return false;
  }
  KdbxCheckpoint committed = checkpoint;
  committed.checksum = ChecksumHex(KdbxCheckpointCanonical(committed));
  const std::string temporary = path + ".tmp";
  const int fd =
      open(temporary.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
  if (fd < 0) {
    if (error)
      *error = "cannot create KDB-X checkpoint";
    return false;
  }
  const std::string payload =
      Json::ObjectValue(
          {{"version", Json::UInteger(committed.version)},
           {"journal_watermark", Json::UInteger(committed.journal_watermark)},
           {"quote_count", Json::UInteger(committed.quote_count)},
           {"bar_count", Json::UInteger(committed.bar_count)},
           {"feature_count", Json::UInteger(committed.feature_count)},
           {"complete", Json::Bool(committed.complete)},
           {"runtime_version", Json::String(committed.runtime_version)},
           {"created_at_ms", Json::Integer(committed.created_at_ms)},
           {"checksum", Json::String(committed.checksum)}})
          .Dump();
  size_t written = 0;
  while (written < payload.size()) {
    const ssize_t count =
        write(fd, payload.data() + written, payload.size() - written);
    if (count < 0 && errno == EINTR)
      continue;
    if (count <= 0) {
      close(fd);
      if (error)
        *error = "KDB-X checkpoint write failed";
      return false;
    }
    written += static_cast<size_t>(count);
  }
  if (fsync(fd) != 0 || close(fd) != 0 ||
      rename(temporary.c_str(), path.c_str()) != 0) {
    close(fd);
    if (error)
      *error = "KDB-X checkpoint commit failed";
    return false;
  }
  if (error)
    error->clear();
  return true;
}

bool LoadKdbxCheckpoint(const std::string& path,
                        KdbxCheckpoint* checkpoint,
                        std::string* error) {
  if (checkpoint == nullptr) {
    if (error)
      *error = "checkpoint output is null";
    return false;
  }
  std::ifstream input(path);
  if (!input.is_open()) {
    if (error)
      *error = "KDB-X checkpoint not found";
    return false;
  }
  const std::string content((std::istreambuf_iterator<char>(input)),
                            std::istreambuf_iterator<char>());
  Json parsed;
  if (!Json::Parse(content, &parsed, error))
    return false;
  auto number = [&parsed](const char* key, uint64_t* out) {
    const Json* value = parsed.Find(key);
    return value != nullptr && value->as_uinteger(out);
  };
  const Json* complete = parsed.Find("complete");
  const Json* version_text = parsed.Find("runtime_version");
  const Json* checksum = parsed.Find("checksum");
  if (complete == nullptr || complete->type() != Json::Type::kBool ||
      version_text == nullptr || version_text->type() != Json::Type::kString ||
      checksum == nullptr || checksum->type() != Json::Type::kString)
    return false;
  KdbxCheckpoint loaded;
  if (!number("version", &loaded.version) ||
      !number("journal_watermark", &loaded.journal_watermark) ||
      !number("quote_count", &loaded.quote_count) ||
      !number("bar_count", &loaded.bar_count) ||
      !number("feature_count", &loaded.feature_count))
    return false;
  loaded.complete = complete->as_bool();
  loaded.runtime_version = version_text->as_string();
  loaded.checksum = checksum->as_string();
  const Json* created = parsed.Find("created_at_ms");
  if (created == nullptr || !created->is_exact_integer())
    return false;
  if (!created->as_integer(&loaded.created_at_ms))
    return false;
  if (loaded.version != 1 || !loaded.complete ||
      loaded.runtime_version.empty() ||
      loaded.checksum != ChecksumHex(KdbxCheckpointCanonical(loaded))) {
    if (error)
      *error = "KDB-X checkpoint failed validation";
    return false;
  }
  *checkpoint = loaded;
  if (error)
    error->clear();
  return true;
}

bool KdbxRecoveryPlan(const KdbxCheckpoint& checkpoint,
                      const std::vector<Event>& journal,
                      std::vector<Event>* replay,
                      std::string* error) {
  if (replay == nullptr) {
    if (error)
      *error = "replay output is null";
    return false;
  }
  replay->clear();
  if (!checkpoint.complete || checkpoint.version != 1) {
    if (error)
      *error = "checkpoint is incomplete";
    return false;
  }
  uint64_t observed = 0;
  for (const Event& event : journal)
    observed = std::max(observed, event.sequence);
  if (checkpoint.journal_watermark > observed) {
    if (error)
      *error = "checkpoint watermark exceeds journalled sequence";
    return false;
  }
  for (const Event& event : journal) {
    if (event.sequence <= checkpoint.journal_watermark)
      continue;
    if (event.type != EventType::kQuote && event.type != EventType::kCandle &&
        event.type != EventType::kSignal)
      continue;
    replay->push_back(event);
  }
  if (error)
    error->clear();
  return true;
}

// ---------------------------------------------------------------------------
// Instrument reference model. Permanent identity is distinct from ticker.
// ---------------------------------------------------------------------------
bool ValidateReferenceInstrument(const ReferenceInstrument& instrument,
                                 std::string* error) {
  auto reject = [error](const char* message) {
    if (error)
      *error = message;
    return false;
  };
  if (instrument.schema_version != 1 || instrument.permanent_id.empty() ||
      instrument.venue.empty() || instrument.isin.empty())
    return reject("permanent identity requires id, venue and ISIN");
  if (!ValidateInstrumentSpec(
          InstrumentSpec{instrument.asset_class,
                         instrument.venue,
                         instrument.calendar_id,
                         instrument.fee_schedule_id,
                         static_cast<double>(instrument.tick_size_minor),
                         static_cast<int>(instrument.lot_size),
                         true},
          error))
    return false;
  if (instrument.currency.size() != 3)
    return reject("currency must be a three-letter code");
  if (instrument.price_scale < 0 || instrument.price_scale > 9 ||
      instrument.quantity_scale < 0 || instrument.quantity_scale > 9)
    return reject("price and quantity scales must be in [0, 9]");
  if (instrument.tick_size_minor <= 0 || instrument.lot_size <= 0 ||
      instrument.contract_multiplier <= 0)
    return reject("tick, lot and multiplier must be positive integers");
  if (instrument.session_model.empty() || instrument.settlement_model.empty() ||
      instrument.margin_model.empty())
    return reject("session, settlement and margin models are required");
  if (instrument.effective_from_ms <= 0 ||
      (instrument.effective_to_ms != 0 &&
       instrument.effective_to_ms < instrument.effective_from_ms))
    return reject("invalid effective interval");
  return true;
}

bool VenueConformanceSupported(const std::string& venue,
                               AssetClass asset_class,
                               const std::string& calendar_id) {
  return venue == "NSE" && asset_class == AssetClass::kCashEquity &&
         calendar_id == "NSE-CALENDAR-v1";
}

// ---------------------------------------------------------------------------
// Asset and compliance contracts. These describe capability boundaries; they
// do not authorise any live route.
// ---------------------------------------------------------------------------
bool ValidateInstrumentSpec(const InstrumentSpec& spec, std::string* error) {
  auto reject = [error](const char* message) {
    if (error)
      *error = message;
    return false;
  };
  if (spec.exchange.empty() || spec.calendar_id.empty() ||
      spec.fee_schedule_id.empty())
    return reject(
        "instrument spec requires exchange, calendar and fee schedule");
  if (!std::isfinite(spec.tick_size) || spec.tick_size <= 0.0)
    return reject("instrument tick size must be finite and positive");
  if (spec.lot_size <= 0)
    return reject("instrument lot size must be positive");
  if (!spec.venue_supported)
    return reject("venue is not supported in this build");
  return true;
}

bool ExecutionProfileSupported(ExecutionProfile profile) {
  return profile == ExecutionProfile::kDaily;
}

bool LiveRouteAuthorized(const ComplianceGateState& gate, std::string* error) {
  if (!gate.written_broker_approval || !gate.compliance_signoff ||
      !gate.operational_risk_signoff || !gate.sandbox_certified ||
      gate.approval_ref.empty()) {
    if (error)
      *error =
          "live route requires written broker, compliance, operational "
          "risk and sandbox approval with a recorded reference";
    return false;
  }
  return false;  // Live execution is unconditionally disabled in this build.
}

// ---------------------------------------------------------------------------
// Canonical market-data pipeline.

namespace {

bool ValidExchangeDate(std::string_view text) {
  if (text.size() != 10 || text[4] != '-' || text[7] != '-')
    return false;
  for (size_t i = 0; i < text.size(); ++i) {
    if (i == 4 || i == 7)
      continue;
    if (text[i] < '0' || text[i] > '9')
      return false;
  }
  const int year = (text[0] - '0') * 1000 + (text[1] - '0') * 100 +
                   (text[2] - '0') * 10 + (text[3] - '0');
  const int month = (text[5] - '0') * 10 + (text[6] - '0');
  const int day = (text[8] - '0') * 10 + (text[9] - '0');
  if (year < 1900 || year > 2200 || month < 1 || month > 12 || day < 1)
    return false;
  static constexpr int days_in_month[] = {
      31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
  const bool leap = (year % 4 == 0 && year % 100 != 0) || (year % 400 == 0);
  const int limit = days_in_month[month - 1] + (month == 2 && leap ? 1 : 0);
  return day <= limit;
}

// 0 < L <= min(O, C) and max(O, C) <= H, volume finite and non-negative.
bool ValidCanonicalBar(const Candle& candle, const std::string& symbol) {
  if (symbol.empty() || !ValidExchangeDate(candle.date))
    return false;
  const double open = candle.open;
  const double high = candle.high;
  const double low = candle.low;
  const double close = candle.close;
  const double volume = candle.volume;
  if (!std::isfinite(open) || !std::isfinite(high) || !std::isfinite(low) ||
      !std::isfinite(close) || !std::isfinite(volume))
    return false;
  return low > 0.0 && low <= std::min(open, close) &&
         std::max(open, close) <= high && close > 0.0 && volume >= 0.0;
}

}  // namespace

bool IsCanonicalExchangeDate(std::string_view text) {
  return ValidExchangeDate(text);
}

// ---------------------------------------------------------------------------
const char* PipelineStatusName(PipelineStatus status) {
  switch (status) {
    case PipelineStatus::kComplete:
      return "COMPLETE";
    case PipelineStatus::kPartial:
      return "PARTIAL";
    case PipelineStatus::kFailed:
      return "FAILED";
    case PipelineStatus::kBlocked:
      return "BLOCKED";
  }
  return "UNKNOWN";
}

MarketDataPipeline::MarketDataPipeline(Journal* journal) : journal_(journal) {
}

bool MarketDataPipeline::IngestQuote(const Quote& quote, std::string* error) {
  if (journal_ == nullptr || !journal_->healthy()) {
    if (error)
      *error = "market-data journal unavailable";
    return false;
  }
  if (quote.symbol.empty() || quote.provider_key.empty() ||
      !(quote.last > 0.0) || !(quote.bid > 0.0) || !(quote.ask >= quote.bid) ||
      quote.ask_qty <= 0 || quote.bid_qty <= 0 || !std::isfinite(quote.last) ||
      !std::isfinite(quote.bid) || !std::isfinite(quote.ask) ||
      !std::isfinite(quote.volume) || quote.volume < 0.0 ||
      quote.exchange_timestamp_ms <= 0 || quote.provider_timestamp_ms <= 0 ||
      quote.receive_timestamp_ms <= 0) {
    if (error)
      *error = "canonical quote failed validation";
    return false;
  }
  Json::Object payload;
  payload["symbol"] = Json::String(quote.symbol);
  payload["provider_key"] = Json::String(quote.provider_key);
  payload["source"] = Json::Number(static_cast<int>(quote.source));
  payload["last"] = Json::Number(quote.last);
  payload["bid"] = Json::Number(quote.bid);
  payload["ask"] = Json::Number(quote.ask);
  payload["bid_qty"] = Json::Integer(quote.bid_qty);
  payload["ask_qty"] = Json::Integer(quote.ask_qty);
  payload["volume"] = Json::Number(quote.volume);
  payload["exchange_timestamp_ms"] = Json::Integer(quote.exchange_timestamp_ms);
  payload["provider_timestamp_ms"] = Json::Integer(quote.provider_timestamp_ms);
  payload["last_trade_timestamp_ms"] =
      Json::Integer(quote.last_trade_timestamp_ms);
  payload["receive_timestamp_ms"] = Json::Integer(quote.receive_timestamp_ms);
  payload["receive_wall_clock_ms"] = Json::Integer(quote.receive_wall_clock_ms);
  payload["quality"] = Json::String(quote.quality);
  Event event;
  event.type = EventType::kQuote;
  event.payload = Json::ObjectValue(payload).Dump();
  return journal_->Append(&event, error);
}

bool MarketDataPipeline::IngestBar(const Candle& candle,
                                   const std::string& symbol,
                                   std::string* error) {
  if (journal_ == nullptr || !journal_->healthy()) {
    if (error)
      *error = "market-data journal unavailable";
    return false;
  }
  if (!ValidCanonicalBar(candle, symbol)) {
    if (error)
      *error = "canonical bar failed OHLCV validation";
    return false;
  }
  Json::Object payload;
  payload["symbol"] = Json::String(symbol);
  payload["date"] = Json::String(candle.date);
  payload["open"] = Json::Number(candle.open);
  payload["high"] = Json::Number(candle.high);
  payload["low"] = Json::Number(candle.low);
  payload["close"] = Json::Number(candle.close);
  payload["volume"] = Json::Number(candle.volume);
  Event event;
  event.type = EventType::kCandle;
  event.payload = Json::ObjectValue(payload).Dump();
  return journal_->Append(&event, error);
}

PipelineResult MarketDataPipeline::Publish(const KdbxCheckpoint& checkpoint,
                                           MarketDataPublisher* publisher,
                                           std::string* error) {
  PipelineResult result;
  if (publisher == nullptr || journal_ == nullptr) {
    result.status = PipelineStatus::kBlocked;
    result.detail = "publisher or journal unavailable";
    if (error)
      *error = result.detail;
    return result;
  }
  std::string replay_error;
  const std::vector<Event> events = journal_->Replay(&replay_error);
  if (!replay_error.empty()) {
    result.status = PipelineStatus::kFailed;
    result.detail = replay_error;
    if (error)
      *error = replay_error;
    return result;
  }
  for (const Event& event : events) {
    if (event.sequence <= checkpoint.journal_watermark)
      continue;
    if (event.type != EventType::kQuote && event.type != EventType::kCandle) {
      ++result.skipped;
      continue;
    }
    Json payload;
    if (!Json::Parse(event.payload, &payload, error)) {
      result.status = PipelineStatus::kFailed;
      result.detail = "undecodable canonical event";
      return result;
    }
    bool ok = false;
    if (event.type == EventType::kQuote) {
      Quote quote;
      const Json* symbol = payload.Find("symbol");
      const Json* key = payload.Find("provider_key");
      quote.symbol = symbol == nullptr ? "" : symbol->as_string();
      quote.provider_key = key == nullptr ? "" : key->as_string();
      auto number = [&payload](const char* field) {
        const Json* value = payload.Find(field);
        return value == nullptr ? 0.0 : value->as_double(0.0);
      };
      quote.last = number("last");
      quote.bid = number("bid");
      quote.ask = number("ask");
      quote.volume = number("volume");
      quote.exchange_timestamp_ms =
          static_cast<int64_t>(number("exchange_timestamp_ms"));
      quote.provider_timestamp_ms =
          static_cast<int64_t>(number("provider_timestamp_ms"));
      quote.last_trade_timestamp_ms =
          static_cast<int64_t>(number("last_trade_timestamp_ms"));
      quote.receive_timestamp_ms =
          static_cast<int64_t>(number("receive_timestamp_ms"));
      quote.receive_wall_clock_ms =
          static_cast<int64_t>(number("receive_wall_clock_ms"));
      const Json* quality = payload.Find("quality");
      quote.quality = quality == nullptr ? "" : quality->as_string();
      ok = publisher->PublishQuote(quote, event.sequence, event.id, error);
    } else {
      Candle candle;
      const Json* date = payload.Find("date");
      candle.date = date == nullptr ? "" : date->as_string();
      auto number = [&payload](const char* field) {
        const Json* value = payload.Find(field);
        return value == nullptr ? 0.0 : value->as_double(0.0);
      };
      candle.open = number("open");
      candle.high = number("high");
      candle.low = number("low");
      candle.close = number("close");
      candle.volume = number("volume");
      const Json* symbol = payload.Find("symbol");
      ok = publisher->PublishBar(
          candle, symbol == nullptr ? "" : symbol->as_string(), error);
    }
    if (!ok) {
      result.status = PipelineStatus::kFailed;
      result.detail =
          "publisher rejected sequence " + std::to_string(event.sequence);
      return result;
    }
    ++result.published;
  }
  result.status = PipelineStatus::kComplete;
  result.detail = "committed events projected";
  if (error)
    error->clear();
  return result;
}

namespace {

std::string DurableId(const char* prefix) {
  std::random_device random;
  std::ostringstream oss;
  oss << prefix << '-';
  for (int i = 0; i < 4; ++i) {
    const uint32_t value = random();
    oss << std::hex << std::setw(8) << std::setfill('0') << value;
  }
  return oss.str();
}
}  // namespace

// ---------------------------------------------------------------------------
// Paper execution: local simulated order lifecycle. No broker endpoint is
// reachable from this class by construction.
// ---------------------------------------------------------------------------
bool ParseFillRecord(const Json& payload, FillRecord* out, std::string* error) {
  auto fail = [error](const char* reason) {
    if (error)
      *error = reason;
    return false;
  };
  if (out == nullptr || payload.type() != Json::Type::kObject)
    return fail("fill record must be an object");
  int64_t parsed_quantity = 0;
  const Json* version = payload.Find("fill_schema_version");
  const Json* id = payload.Find("fill_id");
  const Json* order_id = payload.Find("order_id");
  const Json* symbol = payload.Find("symbol");
  const Json* quantity = payload.Find("fill_quantity");
  const Json* price = payload.Find("fill_price_paise");
  const Json* notional = payload.Find("fill_notional_paise");
  if (version == nullptr || !version->is_exact_integer() ||
      version->as_int(0) != 2 || id == nullptr ||
      id->type() != Json::Type::kString || id->as_string().empty() ||
      order_id == nullptr || order_id->type() != Json::Type::kString ||
      order_id->as_string().empty() || symbol == nullptr ||
      symbol->type() != Json::Type::kString || symbol->as_string().empty() ||
      quantity == nullptr || !quantity->as_integer(&parsed_quantity) ||
      price == nullptr || !price->as_integer(&out->price_paise) ||
      notional == nullptr || !notional->as_integer(&out->notional_paise))
    return fail("malformed fill record");
  if (parsed_quantity <= 0 || parsed_quantity > std::numeric_limits<int>::max())
    return fail("fill quantity out of range");
  out->quantity = static_cast<int>(parsed_quantity);
  out->version = 2;
  out->id = id->as_string();
  out->order_id = order_id->as_string();
  out->symbol = symbol->as_string();
  return true;
}

Json::Object FillRecordJson(const FillRecord& record) {
  Json::Object payload;
  payload["fill_schema_version"] = Json::Integer(record.version);
  payload["fill_id"] = Json::String(record.id);
  payload["order_id"] = Json::String(record.order_id);
  payload["symbol"] = Json::String(record.symbol);
  payload["fill_quantity"] = Json::Integer(record.quantity);
  payload["fill_price_paise"] = Json::Integer(record.price_paise);
  payload["fill_notional_paise"] = Json::Integer(record.notional_paise);
  return payload;
}

bool ValidateFillRecord(const FillRecord& record,
                        const PaperOrder& order,
                        std::string* error) {
  auto fail = [error](const char* reason) {
    if (error)
      *error = reason;
    return false;
  };
  if (record.version != 2 || record.id.empty() || record.order_id != order.id ||
      record.symbol != order.symbol)
    return fail("fill record does not bind to its parent order");
  if (record.quantity <= 0 || record.price_paise <= 0 ||
      record.notional_paise <= 0)
    return fail("fill requires positive quantity and price");
  if (record.quantity > order.quantity - order.filled_quantity)
    return fail("fill quantity exceeds remaining order quantity");
  int64_t expected = 0;
  if (!money_detail::CheckedMultiply(record.price_paise,
                                     static_cast<int64_t>(record.quantity),
                                     &expected) ||
      expected != record.notional_paise)
    return fail("fill notional is not quantity times price");
  for (const PaperOrder::Fill& existing : order.fills) {
    if (existing.id != record.id)
      continue;
    if (existing.quantity != record.quantity ||
        existing.price_paise != record.price_paise ||
        existing.notional_paise != record.notional_paise)
      return fail("conflicting duplicate fill identity");
    return true;  // Identical retry: idempotent no-op.
  }
  return true;
}

bool PaperBroker::ApplyFillRecord(PaperOrder* order,
                                  const FillRecord& record,
                                  std::string* error) {
  if (order == nullptr) {
    if (error)
      *error = "unknown order";
    return false;
  }
  if (!ValidateFillRecord(record, *order, error))
    return false;
  for (const PaperOrder::Fill& existing : order->fills)
    if (existing.id == record.id)
      return true;  // Already applied; idempotent retry.
  int64_t total_notional = 0;
  int64_t total_quantity = 0;
  if (!money_detail::CheckedAdd(order->cumulative_notional_paise,
                                record.notional_paise,
                                &total_notional) ||
      !money_detail::CheckedAdd(static_cast<int64_t>(order->filled_quantity),
                                static_cast<int64_t>(record.quantity),
                                &total_quantity) ||
      total_quantity > static_cast<int64_t>(order->quantity) ||
      total_quantity <= 0) {
    if (error)
      *error = "fill accumulator overflow or overfill";
    return false;
  }
  if (all_fill_ids_.find(record.id) != all_fill_ids_.end()) {
    if (error)
      *error = "fill identity already used by another order";
    return false;
  }
  PaperOrder::Fill lot;
  lot.id = record.id;
  lot.quantity = record.quantity;
  lot.price_paise = record.price_paise;
  lot.notional_paise = record.notional_paise;
  order->fills.push_back(lot);
  order->filled_quantity = static_cast<int>(total_quantity);
  order->cumulative_notional_paise = total_notional;
  // Explicit rounding rule: integer floor division of exact cumulative paise.
  order->average_fill = Money::FromPaise(total_notional / total_quantity);
  if (!order->average_fill.valid()) {
    if (error)
      *error = "computed average fill is invalid";
    return false;
  }
  return true;
}

const char* PaperOrderStateName(PaperOrderState state) {
  switch (state) {
    case PaperOrderState::kIntent:
      return "INTENT";
    case PaperOrderState::kRiskApproved:
      return "RISK_APPROVED";
    case PaperOrderState::kSubmitted:
      return "SUBMITTED";
    case PaperOrderState::kAcknowledged:
      return "ACKNOWLEDGED";
    case PaperOrderState::kWorking:
      return "WORKING";
    case PaperOrderState::kPartiallyFilled:
      return "PARTIALLY_FILLED";
    case PaperOrderState::kFilled:
      return "FILLED";
    case PaperOrderState::kRejected:
      return "REJECTED";
    case PaperOrderState::kCancelled:
      return "CANCELLED";
    case PaperOrderState::kExpired:
      return "EXPIRED";
    case PaperOrderState::kAmbiguous:
      return "AMBIGUOUS";
  }
  return "UNKNOWN";
}

PaperBroker::PaperBroker(Config config,
                         Journal* journal,
                         std::shared_ptr<Portfolio> portfolio,
                         std::shared_ptr<DailyRiskState> risk_state)
    : config_(std::move(config)),
      journal_(journal),
      portfolio_(std::move(portfolio)),
      risk_state_(risk_state == nullptr ? std::make_shared<DailyRiskState>()
                                        : std::move(risk_state)) {
}

namespace {
bool PaperStateMatchesFills(PaperOrderState state, int filled, int quantity) {
  if (filled < 0 || quantity <= 0 || filled > quantity)
    return false;
  switch (state) {
    case PaperOrderState::kIntent:
    case PaperOrderState::kRiskApproved:
    case PaperOrderState::kSubmitted:
    case PaperOrderState::kAcknowledged:
    case PaperOrderState::kWorking:
      return filled == 0;
    case PaperOrderState::kPartiallyFilled:
      return filled > 0 && filled < quantity;
    case PaperOrderState::kFilled:
      return filled == quantity;
    case PaperOrderState::kCancelled:
    case PaperOrderState::kExpired:
    case PaperOrderState::kRejected:
    case PaperOrderState::kAmbiguous:
      return filled == 0 || filled < quantity;
  }
  return false;
}

bool IsValidPaperTransition(PaperOrderState from, PaperOrderState to) {
  if (from == to)
    return true;
  switch (from) {
    case PaperOrderState::kIntent:
      return to == PaperOrderState::kRiskApproved ||
             to == PaperOrderState::kRejected ||
             to == PaperOrderState::kAmbiguous;
    case PaperOrderState::kRiskApproved:
      return to == PaperOrderState::kSubmitted ||
             to == PaperOrderState::kRejected ||
             to == PaperOrderState::kCancelled ||
             to == PaperOrderState::kAmbiguous;
    case PaperOrderState::kSubmitted:
      return to == PaperOrderState::kAcknowledged ||
             to == PaperOrderState::kRejected ||
             to == PaperOrderState::kCancelled ||
             to == PaperOrderState::kAmbiguous;
    case PaperOrderState::kAcknowledged:
    case PaperOrderState::kWorking:
    case PaperOrderState::kPartiallyFilled:
      return to == PaperOrderState::kWorking ||
             to == PaperOrderState::kPartiallyFilled ||
             to == PaperOrderState::kFilled ||
             to == PaperOrderState::kCancelled ||
             to == PaperOrderState::kExpired ||
             to == PaperOrderState::kAmbiguous;
    case PaperOrderState::kAmbiguous:
      return to == PaperOrderState::kCancelled ||
             to == PaperOrderState::kRejected;
    case PaperOrderState::kFilled:
    case PaperOrderState::kRejected:
    case PaperOrderState::kCancelled:
    case PaperOrderState::kExpired:
      return false;
  }
  return false;
}
}  // namespace

bool PaperBroker::Transition(PaperOrder* order,
                             PaperOrderState state,
                             const std::string& reason,
                             std::string* error,
                             const PaperOrder::Fill* fill) {
  if (order == nullptr) {
    if (error)
      *error = "unknown order";
    return false;
  }
  if (!IsValidPaperTransition(order->state, state)) {
    if (error)
      *error = "invalid paper order state transition";
    return false;
  }
  if (!PaperStateMatchesFills(state, order->filled_quantity, order->quantity)) {
    if (error)
      *error = "order state is inconsistent with reconstructed fills";
    return false;
  }
  Json::Object payload;
  payload["order_id"] = Json::String(order->id);
  payload["symbol"] = Json::String(order->symbol);
  payload["side"] =
      Json::String(order->side == OrderSide::kBuy ? "BUY" : "SELL");
  payload["state"] = Json::String(PaperOrderStateName(state));
  payload["schema_version"] = Json::UInteger(1);
  payload["causation_id"] = Json::String(order->causation_id);
  payload["correlation_id"] = Json::String(order->correlation_id);
  payload["observation_ms"] = Json::Integer(0);
  payload["quantity"] = Json::Integer(order->quantity);
  payload["filled_quantity"] = Json::Integer(order->filled_quantity);
  payload["limit_price_paise"] = Json::Integer(order->limit_price.paise());
  payload["instrument_key"] = Json::String(order->instrument_key);
  payload["order_type"] = Json::String(order->order_type);
  payload["time_in_force"] = Json::String(order->time_in_force);
  payload["risk_policy_id"] = Json::String(order->risk_policy_id);
  payload["config_version"] = Json::String(order->config_version);
  payload["execution_model"] = Json::String(order->execution_model);
  if (fill != nullptr) {
    FillRecord record;
    record.version = 2;
    record.id = fill->id;
    record.order_id = order->id;
    record.symbol = order->symbol;
    record.quantity = fill->quantity;
    record.price_paise = fill->price_paise;
    record.notional_paise = fill->notional_paise;
    for (const auto& [key, value] : FillRecordJson(record))
      payload[key] = value;
  }
  payload["reason"] = Json::String(reason);
  Event event;
  event.type = EventType::kPaperIntent;
  event.payload = Json::ObjectValue(payload).Dump();
  if (journal_ != nullptr && !journal_->Append(&event, error))
    return false;
  order->state = state;
  order->reason = reason;
  order->audit.push_back(std::string(PaperOrderStateName(state)) + ": " +
                         reason);
  return true;
}

PaperOrder* PaperBroker::CreateIntent(const std::string& symbol,
                                      OrderSide side,
                                      int quantity,
                                      Money limit_price,
                                      std::string* error) {
  if (symbol.empty() || quantity <= 0 || !limit_price.valid() ||
      !(limit_price > Money::FromPaise(0))) {
    if (error)
      *error = "invalid paper order intent";
    return nullptr;
  }
  PaperOrder order;
  order.id = DurableId("PO");
  order.client_order_id = order.id;
  order.causation_id = order.id;
  order.correlation_id = order.id;
  ++next_id_;
  order.symbol = symbol;
  order.instrument_key = symbol + "-PERMANENT";
  order.side = side;
  order.quantity = quantity;
  order.limit_price = limit_price;
  if (!Transition(
          &order, PaperOrderState::kIntent, "local intent created", error))
    return nullptr;
  const auto inserted = orders_.emplace(order.id, order);
  return &inserted.first->second;
}

bool PaperBroker::RiskApprove(const std::string& id,
                              const RiskAuthorization& authorization,
                              const Quote& market_snapshot,
                              int64_t now_ms,
                              std::string* error) {
  std::lock_guard<std::recursive_mutex> guard(*authority_lock_);
  auto it = orders_.find(id);
  if (it == orders_.end() || it->second.state != PaperOrderState::kIntent) {
    if (error)
      *error = "order is not in INTENT state";
    return false;
  }
  if (!authorization.Verify(it->second, config_, now_ms, error))
    return false;
  if (!authorization.VerifyMarketDataSnapshot(market_snapshot, error))
    return false;
  if (risk_state_ == nullptr ||
      !authorization.VerifyRiskState(*risk_state_, now_ms, error))
    return false;
  if (consumed_authorizations_.find(authorization.signature()) !=
      consumed_authorizations_.end()) {
    if (error)
      *error = "risk authorization already consumed";
    return false;
  }
  // Durable one-time consumption is journalled before the state transition.
  if (journal_ != nullptr) {
    Json::Object payload;
    payload["action"] = Json::String("RISK_AUTH_CONSUMED");
    payload["signature"] = Json::String(authorization.signature());
    payload["order_id"] = Json::String(id);
    Event event;
    event.type = EventType::kHeartbeat;
    event.payload = Json::ObjectValue(payload).Dump();
    if (!journal_->Append(&event, error))
      return false;
  }
  consumed_authorizations_.insert(authorization.signature());
  it->second.risk_policy_id = authorization.policy_version();
  if (!Transition(&it->second,
                  PaperOrderState::kRiskApproved,
                  "approved by authoritative risk capability",
                  error)) {
    consumed_authorizations_.erase(authorization.signature());
    return false;
  }
  return true;
}

bool PaperBroker::Reject(const std::string& id,
                         const std::string& reason,
                         std::string* error) {
  auto it = orders_.find(id);
  if (it == orders_.end()) {
    if (error)
      *error = "unknown order";
    return false;
  }
  return Transition(&it->second, PaperOrderState::kRejected, reason, error);
}

bool PaperBroker::Submit(const std::string& id, std::string* error) {
  auto it = orders_.find(id);
  if (it == orders_.end() ||
      it->second.state != PaperOrderState::kRiskApproved) {
    if (error)
      *error = "order is not risk-approved";
    return false;
  }
  return Transition(
      &it->second, PaperOrderState::kSubmitted, "paper submission", error);
}

bool PaperBroker::Acknowledge(const std::string& id, std::string* error) {
  auto it = orders_.find(id);
  if (it == orders_.end() || it->second.state != PaperOrderState::kSubmitted) {
    if (error)
      *error = "order is not submitted";
    return false;
  }
  return Transition(&it->second,
                    PaperOrderState::kAcknowledged,
                    "simulated acknowledgement",
                    error);
}

bool PaperBroker::ApplyQuote(const std::string& id,
                             const Quote& quote,
                             int64_t now_ms,
                             std::string* error) {
  if (quote.quality == "stale" || quote.quality == "missing_depth" ||
      quote.quality == "cross_provider_mismatch") {
    if (error)
      *error = "quote quality rejects fill";
    return false;
  }
  const int64_t stale_ms =
      static_cast<int64_t>(config_.stale_quote_seconds) * 1000;
  const int64_t quote_ms = std::max({quote.provider_timestamp_ms,
                                     quote.exchange_timestamp_ms,
                                     quote.last_trade_timestamp_ms});
  if (quote_ms <= 0 || now_ms - quote_ms > stale_ms || quote_ms > now_ms) {
    if (error)
      *error = "stale or invalid quote timestamp";
    return false;
  }
  auto it = orders_.find(id);
  if (it == orders_.end() ||
      (it->second.state != PaperOrderState::kAcknowledged &&
       it->second.state != PaperOrderState::kWorking &&
       it->second.state != PaperOrderState::kPartiallyFilled)) {
    if (error)
      *error = "order is not working";
    return false;
  }
  PaperOrder& order = it->second;
  const PaperOrder snapshot = order;
  if (quote.symbol != order.symbol) {
    if (error)
      *error = "quote symbol mismatch";
    return false;
  }
  const bool buy = order.side == OrderSide::kBuy;
  const double executable = buy ? quote.ask : quote.bid;
  const int64_t depth = buy ? quote.ask_qty : quote.bid_qty;
  const bool price_eligible = buy ? executable <= order.limit_price.rupees()
                                  : executable >= order.limit_price.rupees();
  if (!price_eligible || depth <= 0 || !(executable > 0.0) ||
      !std::isfinite(executable)) {
    // A limit touch without depth is explicitly not a fill.
    return true;
  }
  const double participation = config_.max_participation_fraction;
  int fill = static_cast<int>(std::min<int64_t>(
      order.quantity - order.filled_quantity,
      static_cast<int64_t>(
          std::floor(static_cast<double>(depth) * participation))));
  if (fill <= 0)
    return true;
  const Money price = Money::FromRupees(executable);
  if (!price.valid()) {
    if (error)
      *error = "invalid fill price";
    return false;
  }
  FillRecord record;
  record.version = 2;
  record.id = DurableId("FILL");
  record.order_id = order.id;
  record.symbol = order.symbol;
  record.quantity = fill;
  record.price_paise = price.paise();
  if (!money_detail::CheckedMultiply(record.price_paise,
                                     static_cast<int64_t>(fill),
                                     &record.notional_paise)) {
    if (error)
      *error = "fill notional overflow";
    return false;
  }
  if (!ApplyFillRecord(&order, record, error))
    return false;
  const PaperOrder::Fill& lot = order.fills.back();
  const PaperOrderState state = order.filled_quantity >= order.quantity
                                    ? PaperOrderState::kFilled
                                    : PaperOrderState::kPartiallyFilled;
  const std::string reason = "simulated fill " + std::to_string(fill) + " @ " +
                             std::to_string(price.paise());
  // One event records the fill and its resulting state atomically.
  if (!Transition(&order, state, reason, error, &lot)) {
    order = snapshot;
    return false;
  }
  return true;
}

bool PaperBroker::Cancel(const std::string& id, std::string* error) {
  auto it = orders_.find(id);
  if (it == orders_.end() ||
      (it->second.state != PaperOrderState::kWorking &&
       it->second.state != PaperOrderState::kAcknowledged &&
       it->second.state != PaperOrderState::kPartiallyFilled)) {
    if (error)
      *error = "order cannot be cancelled in this state";
    return false;
  }
  return Transition(
      &it->second, PaperOrderState::kCancelled, "cancel acknowledged", error);
}

bool PaperBroker::Expire(const std::string& id, std::string* error) {
  auto it = orders_.find(id);
  if (it == orders_.end()) {
    if (error)
      *error = "unknown order";
    return false;
  }
  if (it->second.state == PaperOrderState::kAmbiguous) {
    if (error)
      *error = "ambiguous order requires operator resolution";
    return false;
  }
  return Transition(
      &it->second, PaperOrderState::kExpired, "session expired", error);
}

bool PaperBroker::MarkAmbiguous(const std::string& id, std::string* error) {
  auto it = orders_.find(id);
  if (it == orders_.end()) {
    if (error)
      *error = "unknown order";
    return false;
  }
  return Transition(&it->second,
                    PaperOrderState::kAmbiguous,
                    "uncertain outcome requires reconciliation",
                    error);
}

bool PaperBroker::Reconcile(const std::string& id, std::string* error) {
  auto it = orders_.find(id);
  if (it == orders_.end() || portfolio_ == nullptr) {
    if (error)
      *error = "unknown order or missing portfolio";
    return false;
  }
  PaperOrder& order = it->second;
  if (order.state == PaperOrderState::kAmbiguous) {
    if (error)
      *error = "ambiguous order requires manual reconciliation";
    return false;
  }
  if (order.state != PaperOrderState::kFilled &&
      order.state != PaperOrderState::kPartiallyFilled)
    return true;
  for (size_t index = order.posted_fill_count; index < order.fills.size();
       ++index) {
    const PaperOrder::Fill& lot = order.fills[index];
    const std::string posting_id = lot.id;
    const std::string marker = "EXEC_TAG " + posting_id;
    if (posted_ids_.find(posting_id) != posted_ids_.end() ||
        portfolio_->HasAudit(marker)) {
      posted_ids_.insert(posting_id);
      order.posted_fill_count = index + 1;
      continue;
    }
    AccountingPosting posting;
    posting.posting_id = posting_id;
    posting.order_id = order.id;
    posting.action = order.side == OrderSide::kBuy ? "BUY" : "SELL";
    posting.symbol = order.symbol;
    posting.quantity = lot.quantity;
    posting.price_paise = lot.price_paise;
    posting.notional_paise = lot.notional_paise;
    posting.costs = EstimateCosts(Money::FromPaise(lot.notional_paise),
                                  config_.execution_delivery,
                                  order.side == OrderSide::kBuy,
                                  config_.slippage_bps);
    if (!posting.costs.usable()) {
      if (error)
        *error = "reconciliation cost calculation invalid";
      return false;
    }
    Portfolio trial = *portfolio_;
    if (!ApplyAccountingPosting(posting, &trial, error)) {
      if (error)
        *error = std::string("posting preflight failed: ") + *error;
      return false;
    }
    if (!AppendAccountingPosting(journal_, posting, error))
      return false;
    posted_ids_.insert(posting_id);
    if (!ApplyAccountingPosting(posting, portfolio_.get(), error)) {
      posted_ids_.erase(posting_id);
      return false;
    }
    order.posted_fill_count = index + 1;
  }
  if (order.filled_quantity >= order.quantity &&
      order.state != PaperOrderState::kFilled) {
    return Transition(
        &order, PaperOrderState::kFilled, "all fill lots reconciled", error);
  }
  return true;
}

PaperOrderState ParsePaperState(const std::string& name) {
  static const std::map<std::string, PaperOrderState> names = {
      {"INTENT", PaperOrderState::kIntent},
      {"RISK_APPROVED", PaperOrderState::kRiskApproved},
      {"SUBMITTED", PaperOrderState::kSubmitted},
      {"ACKNOWLEDGED", PaperOrderState::kAcknowledged},
      {"WORKING", PaperOrderState::kWorking},
      {"PARTIALLY_FILLED", PaperOrderState::kPartiallyFilled},
      {"FILLED", PaperOrderState::kFilled},
      {"REJECTED", PaperOrderState::kRejected},
      {"CANCELLED", PaperOrderState::kCancelled},
      {"EXPIRED", PaperOrderState::kExpired},
      {"AMBIGUOUS", PaperOrderState::kAmbiguous},
  };
  const auto it = names.find(name);
  return it == names.end() ? PaperOrderState::kAmbiguous : it->second;
}

bool PaperBroker::ResolveAmbiguous(const std::string& id,
                                   PaperOrderState resolved_state,
                                   const std::string& operator_id,
                                   const std::string& reason,
                                   std::string* error) {
  auto it = orders_.find(id);
  if (it == orders_.end()) {
    if (error)
      *error = "unknown order";
    return false;
  }
  if (it->second.state != PaperOrderState::kAmbiguous) {
    if (error)
      *error = "order is not in AMBIGUOUS state";
    return false;
  }
  if (resolved_state != PaperOrderState::kCancelled &&
      resolved_state != PaperOrderState::kRejected) {
    if (error)
      *error = "operator resolution must cancel or reject the order";
    return false;
  }
  if (operator_id.empty() || reason.empty()) {
    if (error)
      *error = "operator identity and reason are required";
    return false;
  }
  const std::string detail = "operator=" + operator_id + " reason=" + reason;
  return Transition(&it->second, resolved_state, detail, error);
}

bool PaperBroker::RehydrateFromJournal(std::string* error) {
  auto fail = [this, error](const char* reason) {
    if (error)
      *error = reason;
    orders_.clear();
    posted_ids_.clear();
    all_fill_ids_.clear();
    return false;
  };
  if (journal_ == nullptr)
    return fail("rehydration requires a journal");
  const std::vector<Event> events = journal_->Replay(error);
  if (error != nullptr && !error->empty())
    return false;
  orders_.clear();
  posted_ids_.clear();
  all_fill_ids_.clear();
  for (const Event& event : events) {
    Json payload;
    if (!Json::Parse(event.payload, &payload, error))
      return fail("paper journal event is not valid JSON");
    if (event.type == EventType::kHeartbeat) {
      const Json* action = payload.Find("action");
      const Json* signature = payload.Find("signature");
      if (action != nullptr && action->as_string() == "RISK_AUTH_CONSUMED") {
        if (signature == nullptr || signature->type() != Json::Type::kString ||
            signature->as_string().empty())
          return fail("malformed risk authorization consumption record");
        if (!consumed_authorizations_.insert(signature->as_string()).second)
          return fail("duplicate risk authorization consumption record");
      }
      continue;
    }
    if (event.type == EventType::kAccounting) {
      AccountingPosting posting;
      if (!ParseAccountingPosting(event, &posting, error))
        return false;
      const auto already = posted_ids_.insert(posting.posting_id);
      if (!already.second)
        return fail("duplicate accounting posting identity");
      continue;
    }
    if (event.type == EventType::kFill) {
      FillRecord record;
      if (!ParseFillRecord(payload, &record, error))
        return false;
      auto order = orders_.find(record.order_id);
      if (order == orders_.end())
        return fail("fill event references missing parent order");
      if (all_fill_ids_.find(record.id) != all_fill_ids_.end())
        return fail("fill identity reused across orders in journal");
      if (!ApplyFillRecord(&order->second, record, error))
        return fail("fill reducer rejected event");
      all_fill_ids_.insert(record.id);
      continue;
    }
    if (event.type != EventType::kPaperIntent)
      continue;
    PaperOrder order;
    const Json* order_id = payload.Find("order_id");
    const Json* symbol = payload.Find("symbol");
    const Json* state = payload.Find("state");
    const Json* side = payload.Find("side");
    const Json* limit = payload.Find("limit_price_paise");
    const Json* quantity = payload.Find("quantity");
    if (order_id == nullptr || symbol == nullptr || state == nullptr ||
        side == nullptr || limit == nullptr || quantity == nullptr ||
        order_id->type() != Json::Type::kString ||
        symbol->type() != Json::Type::kString ||
        side->type() != Json::Type::kString ||
        state->type() != Json::Type::kString)
      return fail("malformed paper order transition");
    order.id = order_id->as_string();
    order.client_order_id = order.id;
    order.causation_id = order.id;
    order.correlation_id = order.id;
    order.symbol = symbol->as_string();
    if (side->as_string() != "BUY" && side->as_string() != "SELL")
      return fail("unknown order side in journal");
    order.side =
        side->as_string() == "SELL" ? OrderSide::kSell : OrderSide::kBuy;
    int64_t order_limit = 0;
    int64_t order_quantity = 0;
    if (!limit->as_integer(&order_limit) ||
        !quantity->as_integer(&order_quantity) || order_limit <= 0 ||
        order_quantity <= 0 || order_quantity > std::numeric_limits<int>::max())
      return fail("invalid order limit or quantity in journal");
    order.limit_price = Money::FromPaise(order_limit);
    order.quantity = static_cast<int>(order_quantity);
    if (!order.limit_price.valid())
      return fail("invalid limit price in journal");
    auto optional_text = [&payload](const char* key, std::string* out) {
      const Json* value = payload.Find(key);
      if (value == nullptr || value->type() != Json::Type::kString)
        return false;
      *out = value->as_string();
      return true;
    };
    optional_text("instrument_key", &order.instrument_key);
    optional_text("order_type", &order.order_type);
    optional_text("time_in_force", &order.time_in_force);
    if (order.order_type != "LIMIT" || order.time_in_force != "DAY")
      return fail("unsupported order type or time-in-force in journal");
    optional_text("risk_policy_id", &order.risk_policy_id);
    optional_text("config_version", &order.config_version);
    optional_text("execution_model", &order.execution_model);
    const PaperOrderState next_state = ParsePaperState(state->as_string());
    auto existing = orders_.find(order.id);
    if (existing == orders_.end()) {
      if (next_state != PaperOrderState::kIntent)
        return fail("first order transition is not INTENT");
      orders_.emplace(order.id, order);
    } else {
      PaperOrder& restored = existing->second;
      if (restored.symbol != order.symbol || restored.side != order.side ||
          restored.quantity != order.quantity ||
          restored.limit_price != order.limit_price)
        return fail("order identity mutated across transitions");
      if (!IsValidPaperTransition(restored.state, next_state))
        return fail("illegal paper order state transition in journal");
      restored.state = next_state;
      restored.risk_policy_id = order.risk_policy_id;
      restored.instrument_key = order.instrument_key;
      restored.order_type = order.order_type;
      restored.time_in_force = order.time_in_force;
      restored.config_version = order.config_version;
      restored.execution_model = order.execution_model;
    }
    PaperOrder& restored = orders_[order.id];
    const Json* fill_id = payload.Find("fill_id");
    if (fill_id != nullptr) {
      FillRecord record;
      if (!ParseFillRecord(payload, &record, error))
        return fail("malformed fill record in order transition");
      const size_t before_fills = restored.fills.size();
      if (!ApplyFillRecord(&restored, record, error))
        return fail("fill reducer rejected event");
      if (restored.fills.size() > before_fills &&
          all_fill_ids_.find(record.id) != all_fill_ids_.end())
        return fail("fill identity reused across orders in journal");
      all_fill_ids_.insert(record.id);
    }
    const Json* recorded_filled = payload.Find("filled_quantity");
    int64_t decoded_filled = 0;
    if (recorded_filled == nullptr ||
        !recorded_filled->as_integer(&decoded_filled) ||
        decoded_filled != restored.filled_quantity)
      return fail("recorded filled quantity differs from reconstructed fills");
    if (!PaperStateMatchesFills(
            restored.state, restored.filled_quantity, restored.quantity))
      return fail("journal state is inconsistent with reconstructed fills");
    next_id_ = std::max(next_id_, static_cast<uint64_t>(orders_.size() + 1));
  }
  for (auto& [id, order] : orders_) {
    (void)id;
    size_t posted = 0;
    for (const auto& lot : order.fills) {
      if (posted_ids_.find(lot.id) == posted_ids_.end())
        break;
      ++posted;
    }
    order.posted_fill_count = posted;
    if (!order.fills.empty() && order.filled_quantity > 0) {
      int64_t total = 0;
      if (!money_detail::CheckedAdd(
              0, order.cumulative_notional_paise, &total) ||
          !order.average_fill.valid())
        return fail("rehydrated fill arithmetic is invalid");
      order.average_fill =
          Money::FromPaise(order.cumulative_notional_paise /
                           static_cast<int64_t>(order.filled_quantity));
      if (!order.average_fill.valid())
        return fail("rehydrated average fill is invalid");
    }
  }
  return true;
}

const PaperOrder* PaperBroker::Find(const std::string& id) const {
  const auto it = orders_.find(id);
  return it == orders_.end() ? nullptr : &it->second;
}

// ---------------------------------------------------------------------------
// Canonical accounting postings.
// ---------------------------------------------------------------------------
namespace {
Json::Object CostObject(const CostBreakdown& costs) {
  auto integer = [](int64_t value) { return Json::Integer(value); };
  return Json::Object({{"brokerage", integer(costs.brokerage.paise())},
                       {"stt", integer(costs.stt.paise())},
                       {"exchange_fee", integer(costs.exchange_fee.paise())},
                       {"gst", integer(costs.gst.paise())},
                       {"sebi", integer(costs.sebi.paise())},
                       {"stamp", integer(costs.stamp.paise())},
                       {"dp", integer(costs.dp.paise())},
                       {"slippage", integer(costs.slippage.paise())},
                       {"total", integer(costs.total.paise())}});
}

bool ReadCostObject(const Json& object, CostBreakdown* costs) {
  const Json* source = object.Find("recorded_costs_paise");
  if (source == nullptr || source->type() != Json::Type::kObject)
    return false;
  auto read = [source](const char* field, Money* out) {
    const Json* value = source->Find(field);
    if (value == nullptr)
      return false;
    int64_t paise = 0;
    if (!value->as_integer(&paise))
      return false;
    *out = Money::FromPaise(paise);
    return out->valid();
  };
  if (!read("brokerage", &costs->brokerage) || !read("stt", &costs->stt) ||
      !read("exchange_fee", &costs->exchange_fee) ||
      !read("gst", &costs->gst) || !read("sebi", &costs->sebi) ||
      !read("stamp", &costs->stamp) || !read("dp", &costs->dp) ||
      !read("slippage", &costs->slippage) || !read("total", &costs->total))
    return false;
  costs->status = CalculationStatus::kCalculated;
  return costs->usable();
}
}  // namespace

std::string AccountingPayloadDigest(const AccountingPosting& posting) {
  std::ostringstream canonical;
  canonical << posting.schema_version << '|' << posting.posting_id << '|'
            << posting.order_id << '|' << posting.action << '|'
            << posting.symbol << '|' << posting.quantity << '|'
            << posting.price_paise << '|' << posting.notional_paise << '|'
            << posting.amount_paise << '|' << posting.costs.brokerage.paise()
            << '|' << posting.costs.stt.paise() << '|'
            << posting.costs.exchange_fee.paise() << '|'
            << posting.costs.gst.paise() << '|' << posting.costs.sebi.paise()
            << '|' << posting.costs.stamp.paise() << '|'
            << posting.costs.dp.paise() << '|' << posting.costs.slippage.paise()
            << '|' << posting.costs.total.paise();
  return ChecksumHex(canonical.str());
}

bool ValidateAccountingPosting(const AccountingPosting& posting,
                               std::string* error) {
  auto reject = [error](const char* message) {
    if (error)
      *error = message;
    return false;
  };
  if (posting.schema_version != 1 || posting.posting_id.empty() ||
      posting.symbol.empty())
    return reject("accounting posting identity invalid");
  if (posting.action != "BUY" && posting.action != "SELL" &&
      posting.action != "EXPENSE" && posting.action != "FLOW")
    return reject("accounting posting action invalid");
  const bool trade = posting.action == "BUY" || posting.action == "SELL";
  if (trade) {
    if (posting.quantity <= 0 || posting.price_paise <= 0 ||
        posting.notional_paise <= 0)
      return reject("accounting posting trade amounts invalid");
    int64_t expected = 0;
    if (!money_detail::CheckedMultiply(posting.price_paise,
                                       static_cast<int64_t>(posting.quantity),
                                       &expected) ||
        expected != posting.notional_paise)
      return reject("accounting posting notional mismatch");
    if (!posting.costs.usable())
      return reject("accounting posting costs unavailable");
    int64_t sum = 0;
    if (!money_detail::CheckedAdd(sum, posting.costs.brokerage.paise(), &sum) ||
        !money_detail::CheckedAdd(sum, posting.costs.stt.paise(), &sum) ||
        !money_detail::CheckedAdd(
            sum, posting.costs.exchange_fee.paise(), &sum) ||
        !money_detail::CheckedAdd(sum, posting.costs.gst.paise(), &sum) ||
        !money_detail::CheckedAdd(sum, posting.costs.sebi.paise(), &sum) ||
        !money_detail::CheckedAdd(sum, posting.costs.stamp.paise(), &sum) ||
        !money_detail::CheckedAdd(sum, posting.costs.dp.paise(), &sum) ||
        !money_detail::CheckedAdd(sum, posting.costs.slippage.paise(), &sum) ||
        sum != posting.costs.total.paise())
      return reject("accounting posting cost total mismatch");
  } else {
    if (posting.amount_paise < 0)
      return reject("accounting posting amount invalid");
  }
  return true;
}

bool RebuildPortfolioFromJournal(Journal* journal,
                                 Money initial_capital,
                                 Portfolio* out,
                                 std::string* error) {
  if (journal == nullptr || out == nullptr) {
    if (error)
      *error = "replay requires a journal and portfolio";
    return false;
  }
  Portfolio portfolio(initial_capital);
  const std::vector<Event> events = journal->Replay(error);
  if (!error->empty())
    return false;
  std::map<std::string, std::string> seen;
  for (const Event& event : events) {
    if (event.type != EventType::kAccounting)
      continue;
    AccountingPosting posting;
    if (!ParseAccountingPosting(event, &posting, error))
      return false;
    const std::string digest = AccountingPayloadDigest(posting);
    const auto prior = seen.find(posting.posting_id);
    if (prior != seen.end()) {
      if (prior->second != digest) {
        if (error)
          *error = "conflicting accounting posting ID at sequence " +
                   std::to_string(event.sequence);
        return false;
      }
      continue;  // Exact duplicate is an idempotent retry.
    }
    seen[posting.posting_id] = digest;
    if (!ApplyAccountingPosting(posting, &portfolio, error)) {
      if (error)
        *error = "accounting replay failed at sequence " +
                 std::to_string(event.sequence) + ": " + *error;
      return false;
    }
  }
  *out = portfolio;
  if (error)
    error->clear();
  return true;
}

bool AppendAccountingPosting(Journal* journal,
                             const AccountingPosting& posting,
                             std::string* error) {
  if (journal == nullptr || !ValidateAccountingPosting(posting, error))
    return false;
  Json::Object payload;
  payload["schema_version"] = Json::UInteger(posting.schema_version);
  payload["posting_id"] = Json::String(posting.posting_id);
  payload["order_id"] = Json::String(posting.order_id);
  payload["action"] = Json::String(posting.action);
  payload["symbol"] = Json::String(posting.symbol);
  payload["qty"] = Json::Integer(posting.quantity);
  payload["price_paise"] = Json::Integer(posting.price_paise);
  payload["notional_paise"] = Json::Integer(posting.notional_paise);
  payload["amount_paise"] = Json::Integer(posting.amount_paise);
  payload["recorded_costs_paise"] =
      Json::ObjectValue(CostObject(posting.costs));
  Event event;
  event.type = EventType::kAccounting;
  event.payload = Json::ObjectValue(payload).Dump();
  return journal->Append(&event, error);
}

bool ParseAccountingPosting(const Event& event,
                            AccountingPosting* posting,
                            std::string* error) {
  if (posting == nullptr) {
    if (error)
      *error = "posting output is null";
    return false;
  }
  Json payload;
  if (!Json::Parse(event.payload, &payload, error))
    return false;
  auto integer = [&payload](const char* key, int64_t* out) {
    const Json* value = payload.Find(key);
    return value != nullptr && value->as_integer(out);
  };
  auto text = [&payload](const char* key, std::string* out) {
    const Json* value = payload.Find(key);
    if (value == nullptr || value->type() != Json::Type::kString)
      return false;
    *out = value->as_string();
    return true;
  };
  AccountingPosting parsed;
  int64_t schema = 0;
  int64_t quantity = 0;
  if (!integer("schema_version", &schema) || schema != 1 ||
      !text("posting_id", &parsed.posting_id) ||
      !text("order_id", &parsed.order_id) || !text("action", &parsed.action) ||
      !text("symbol", &parsed.symbol) || !integer("qty", &quantity) ||
      quantity <= 0 || quantity > std::numeric_limits<int>::max() ||
      !integer("price_paise", &parsed.price_paise) ||
      !integer("notional_paise", &parsed.notional_paise) ||
      !integer("amount_paise", &parsed.amount_paise) ||
      !ReadCostObject(payload, &parsed.costs)) {
    if (error)
      *error = "accounting posting schema invalid";
    return false;
  }
  if (parsed.posting_id.empty() || parsed.symbol.empty() ||
      (parsed.action != "BUY" && parsed.action != "SELL" &&
       parsed.action != "EXPENSE" && parsed.action != "FLOW")) {
    if (error)
      *error = "accounting posting fields invalid";
    return false;
  }
  parsed.quantity = static_cast<int>(quantity);
  if ((parsed.action == "BUY" || parsed.action == "SELL") &&
      (parsed.price_paise <= 0 || parsed.notional_paise <= 0)) {
    if (error)
      *error = "accounting posting has invalid trade amounts";
    return false;
  }
  if (!ValidateAccountingPosting(parsed, error))
    return false;
  *posting = parsed;
  return true;
}

bool ApplyAccountingPosting(const AccountingPosting& posting,
                            Portfolio* portfolio,
                            std::string* error) {
  if (portfolio == nullptr || posting.schema_version != 1) {
    if (error)
      *error = "accounting reducer requires a portfolio and schema 1";
    return false;
  }
  if (posting.action == "BUY") {
    return portfolio->Buy(posting.symbol,
                          posting.quantity,
                          Money::FromPaise(posting.price_paise),
                          posting.costs,
                          error,
                          posting.posting_id);
  }
  if (posting.action == "SELL") {
    return portfolio->Sell(posting.symbol,
                           posting.quantity,
                           Money::FromPaise(posting.price_paise),
                           posting.costs,
                           error,
                           posting.posting_id);
  }
  if (posting.action == "EXPENSE") {
    return portfolio->RecordOperatingExpense(
        Money::FromPaise(posting.amount_paise), error);
  }
  return portfolio->RecordExternalFlow(
      Money::FromPaise(posting.amount_paise), posting.posting_id, error);
}

// ---------------------------------------------------------------------------
// Durable daily risk state. The journal is authoritative; snapshots are
// checksummed recovery accelerators carrying a journal watermark.
// ---------------------------------------------------------------------------
std::string RiskSnapshotCanonical(const RiskSnapshot& snapshot) {
  std::ostringstream oss;
  oss << snapshot.version << '|' << snapshot.account_id << '|'
      << snapshot.session_id << '|' << snapshot.policy_version << '|'
      << snapshot.journal_sequence << '|' << snapshot.starting_equity_paise
      << '|' << snapshot.current_equity_paise << '|'
      << snapshot.peak_equity_paise << '|' << snapshot.low_equity_paise << '|'
      << snapshot.realized_pnl_paise << '|' << snapshot.unrealized_pnl_paise
      << '|' << (snapshot.halted ? 1 : 0) << '|' << snapshot.halt_reason << '|'
      << snapshot.journal_identity << '|' << snapshot.watermark_event_checksum;
  return oss.str();
}

std::string JournalIdentity(const std::vector<Event>& events,
                            uint64_t through_sequence) {
  std::ostringstream canonical;
  if (through_sequence > 0) {
    for (const Event& event : events) {
      if (event.sequence > through_sequence)
        continue;
      canonical << event.sequence << '|' << event.id << '|'
                << event.timestamp_ms << '|' << event.checksum << ';';
    }
  }
  return ChecksumHex(canonical.str());
}

bool FillRiskCheckpointIdentity(RiskSnapshot* snapshot,
                                const std::vector<Event>& events,
                                std::string* error) {
  if (snapshot == nullptr) {
    if (error)
      *error = "checkpoint output is null";
    return false;
  }
  snapshot->journal_identity =
      JournalIdentity(events, snapshot->journal_sequence);
  snapshot->watermark_event_checksum.clear();
  if (snapshot->journal_sequence == 0)
    return true;
  for (const Event& event : events) {
    if (event.sequence != snapshot->journal_sequence)
      continue;
    snapshot->watermark_event_checksum = event.checksum;
    return true;
  }
  if (error)
    *error = "checkpoint watermark is absent from the journal";
  return false;
}

bool VerifyRiskCheckpointIdentity(const RiskSnapshot& snapshot,
                                  const std::vector<Event>& events,
                                  const std::string& expected_account,
                                  std::string* error) {
  auto reject = [error](const char* message) {
    if (error)
      *error = message;
    return false;
  };
  if (snapshot.journal_identity.empty() ||
      snapshot.journal_identity !=
          JournalIdentity(events, snapshot.journal_sequence))
    return reject("checkpoint journal identity mismatch");
  if (!expected_account.empty() && snapshot.account_id != expected_account)
    return reject("checkpoint account identity mismatch");
  uint64_t highest = 0;
  for (const Event& event : events) {
    highest = std::max(highest, event.sequence);
    if (event.sequence != snapshot.journal_sequence)
      continue;
    if (snapshot.journal_sequence > 0 &&
        snapshot.watermark_event_checksum != event.checksum)
      return reject("checkpoint watermark event checksum mismatch");
  }
  if (snapshot.journal_sequence > highest)
    return reject("checkpoint watermark ahead of journal");
  if (snapshot.journal_sequence > 0 &&
      snapshot.watermark_event_checksum.empty())
    return reject("checkpoint watermark event is absent");
  return true;
}

bool SaveRiskSnapshot(const std::string& path,
                      const RiskSnapshot& snapshot,
                      std::string* error) {
  if (path.empty() || snapshot.version != 1) {
    if (error)
      *error = "invalid risk snapshot identity";
    return false;
  }
  RiskSnapshot committed = snapshot;
  committed.checksum = ChecksumHex(RiskSnapshotCanonical(committed));
  const std::string temporary = path + ".tmp";
  const int fd =
      open(temporary.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
  if (fd < 0) {
    if (error)
      *error = "cannot create risk snapshot";
    return false;
  }
  const std::string payload =
      Json::ObjectValue(
          {{"version", Json::UInteger(committed.version)},
           {"account_id", Json::String(committed.account_id)},
           {"session_id", Json::String(committed.session_id)},
           {"policy_version", Json::String(committed.policy_version)},
           {"journal_sequence", Json::UInteger(committed.journal_sequence)},
           {"starting_equity_paise",
            Json::Integer(committed.starting_equity_paise)},
           {"current_equity_paise",
            Json::Integer(committed.current_equity_paise)},
           {"peak_equity_paise", Json::Integer(committed.peak_equity_paise)},
           {"low_equity_paise", Json::Integer(committed.low_equity_paise)},
           {"realized_pnl_paise", Json::Integer(committed.realized_pnl_paise)},
           {"unrealized_pnl_paise",
            Json::Integer(committed.unrealized_pnl_paise)},
           {"halted", Json::Bool(committed.halted)},
           {"halt_reason", Json::String(committed.halt_reason)},
           {"journal_identity", Json::String(committed.journal_identity)},
           {"watermark_event_checksum",
            Json::String(committed.watermark_event_checksum)},
           {"checksum", Json::String(committed.checksum)}})
          .Dump();
  size_t written = 0;
  while (written < payload.size()) {
    const ssize_t count =
        write(fd, payload.data() + written, payload.size() - written);
    if (count < 0 && errno == EINTR)
      continue;
    if (count <= 0) {
      close(fd);
      if (error)
        *error = "risk snapshot write failed";
      return false;
    }
    written += static_cast<size_t>(count);
  }
  if (fsync(fd) != 0 || close(fd) != 0 ||
      rename(temporary.c_str(), path.c_str()) != 0) {
    close(fd);
    if (error)
      *error = "risk snapshot commit failed";
    return false;
  }
  const size_t slash = path.find_last_of('/');
  const std::string directory =
      slash == std::string::npos ? "." : path.substr(0, slash);
  const int dir_fd =
      open(directory.c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC);
  if (dir_fd >= 0) {
    if (fsync(dir_fd) != 0) {
      close(dir_fd);
      if (error)
        *error = "risk snapshot directory synchronization failed";
      return false;
    }
    close(dir_fd);
  }
  if (error)
    error->clear();
  return true;
}

bool LoadRiskSnapshot(const std::string& path,
                      RiskSnapshot* snapshot,
                      std::string* error) {
  if (snapshot == nullptr) {
    if (error)
      *error = "snapshot output is null";
    return false;
  }
  std::ifstream input(path);
  if (!input.is_open()) {
    if (error)
      *error = "risk snapshot not found";
    return false;
  }
  const std::string content((std::istreambuf_iterator<char>(input)),
                            std::istreambuf_iterator<char>());
  Json parsed;
  if (!Json::Parse(content, &parsed, error))
    return false;
  auto integer = [&parsed](const char* key, int64_t* out) {
    const Json* value = parsed.Find(key);
    return value != nullptr && value->as_integer(out);
  };
  auto text = [&parsed](const char* key, std::string* out) {
    const Json* value = parsed.Find(key);
    if (value == nullptr || value->type() != Json::Type::kString)
      return false;
    *out = value->as_string();
    return true;
  };
  RiskSnapshot loaded;
  int64_t sequence = 0;
  if (!integer("version", &sequence) || sequence != 1 ||
      !integer("journal_sequence", &sequence) || sequence < 0)
    return false;
  loaded.journal_sequence = static_cast<uint64_t>(sequence);
  if (!text("account_id", &loaded.account_id) ||
      !text("session_id", &loaded.session_id) ||
      !text("policy_version", &loaded.policy_version) ||
      !text("halt_reason", &loaded.halt_reason) ||
      !text("journal_identity", &loaded.journal_identity) ||
      !text("watermark_event_checksum", &loaded.watermark_event_checksum) ||
      !text("checksum", &loaded.checksum))
    return false;
  if (!integer("starting_equity_paise", &loaded.starting_equity_paise) ||
      !integer("current_equity_paise", &loaded.current_equity_paise) ||
      !integer("peak_equity_paise", &loaded.peak_equity_paise) ||
      !integer("low_equity_paise", &loaded.low_equity_paise) ||
      !integer("realized_pnl_paise", &loaded.realized_pnl_paise) ||
      !integer("unrealized_pnl_paise", &loaded.unrealized_pnl_paise))
    return false;
  const Json* halted = parsed.Find("halted");
  if (halted == nullptr || halted->type() != Json::Type::kBool)
    return false;
  loaded.halted = halted->as_bool();
  if (loaded.account_id.empty() || loaded.session_id.empty() ||
      loaded.policy_version.empty())
    return false;
  if (loaded.checksum != ChecksumHex(RiskSnapshotCanonical(loaded))) {
    if (error)
      *error = "risk snapshot checksum mismatch";
    return false;
  }
  *snapshot = loaded;
  if (error)
    error->clear();
  return true;
}

bool AppendRiskEvent(Journal* journal,
                     const std::string& action,
                     DailyRiskState* state,
                     int64_t now_ms,
                     std::string* error) {
  if (journal == nullptr || state == nullptr || !journal->healthy()) {
    if (error)
      *error = "risk event requires a healthy journal";
    return false;
  }
  static const std::set<std::string> allowed = {"RISK_SESSION_INITIALIZED",
                                                "EQUITY_MARKED",
                                                "KILL_SWITCH_ACTIVATED",
                                                "DAILY_LIMIT_APPROACHED"};
  if (allowed.find(action) == allowed.end()) {
    if (error)
      *error = "unknown risk event action";
    return false;
  }
  Json::Object payload;
  payload["action"] = Json::String(action);
  payload["session_id"] = Json::String(state->date);
  payload["policy_version"] = Json::String(state->policy_version);
  payload["starting_equity_paise"] =
      Json::Integer(state->starting_equity.paise());
  payload["current_equity_paise"] =
      Json::Integer(state->current_equity.paise());
  payload["peak_equity_paise"] = Json::Integer(state->peak_equity.paise());
  payload["low_equity_paise"] = Json::Integer(state->low_equity.paise());
  payload["halted"] = Json::Bool(state->halted);
  payload["halt_reason"] = Json::String(state->halt_reason);
  Event event;
  event.type = EventType::kRiskDecision;
  event.timestamp_ms = now_ms;
  event.payload = Json::ObjectValue(payload).Dump();
  if (!journal->Append(&event, error))
    return false;
  state->journal_sequence = event.sequence;
  return true;
}

bool RecoverRiskState(const std::string& snapshot_path,
                      const std::string& journal_path,
                      DailyRiskState* state,
                      std::string* error) {
  return RecoverRiskState(snapshot_path, journal_path, state, "", error);
}

bool RecoverRiskState(const std::string& snapshot_path,
                      const std::string& journal_path,
                      DailyRiskState* state,
                      const std::string& expected_account,
                      std::string* error) {
  Journal journal;
  if (!journal.Open(journal_path, error))
    return false;
  const bool recovered = RecoverRiskStateFromJournal(
      snapshot_path, &journal, state, expected_account, error);
  journal.Close();
  return recovered;
}

bool RecoverRiskStateFromJournal(const std::string& snapshot_path,
                                 Journal* journal,
                                 DailyRiskState* state,
                                 const std::string& expected_account,
                                 std::string* error) {
  if (state == nullptr || journal == nullptr) {
    if (error)
      *error = "risk recovery requires a journal and output";
    return false;
  }
  RiskSnapshot snapshot;
  bool have_snapshot = LoadRiskSnapshot(snapshot_path, &snapshot, error);
  if (!have_snapshot) {
    // A missing or corrupt snapshot is rebuilt from the authoritative journal.
    if (error)
      error->clear();
    snapshot = RiskSnapshot();
    snapshot.account_id = "PRIMARY";
    snapshot.policy_version = "RISK-2026-11-26-v1";
  }
  std::string replay_error;
  const std::vector<Event> events = journal->Replay(&replay_error);
  if (!replay_error.empty()) {
    if (error)
      *error = "risk recovery journal replay failed: " + replay_error;
    return false;
  }
  if (have_snapshot &&
      !VerifyRiskCheckpointIdentity(snapshot, events, expected_account, error))
    return false;
  DailyRiskState recovered;
  recovered.account_id = snapshot.account_id;
  recovered.policy_version = snapshot.policy_version;
  recovered.journal_sequence = snapshot.journal_sequence;
  int64_t starting = 0;
  int64_t current = 0;
  int64_t peak = 0;
  int64_t low = 0;
  if (have_snapshot) {
    starting = snapshot.starting_equity_paise;
    current = snapshot.current_equity_paise;
    peak = snapshot.peak_equity_paise;
    low = snapshot.low_equity_paise;
    recovered.realized_pnl = Money::FromPaise(snapshot.realized_pnl_paise);
    recovered.unrealized_pnl = Money::FromPaise(snapshot.unrealized_pnl_paise);
    recovered.halted = snapshot.halted;
    recovered.halt_reason = snapshot.halt_reason;
    recovered.date = snapshot.session_id;
  }
  bool applied_risk_event = false;
  for (const Event& event : events) {
    if (event.type != EventType::kRiskDecision ||
        event.sequence <= recovered.journal_sequence)
      continue;
    Json payload;
    if (!Json::Parse(event.payload, &payload, error))
      return false;
    const Json* action = payload.Find("action");
    if (action == nullptr) {
      if (error)
        *error = "risk event missing action";
      return false;
    }
    const std::string name = action->as_string();
    auto paise = [&payload](const char* key, int64_t fallback) {
      const Json* value = payload.Find(key);
      return value == nullptr ? fallback : value->as_int(fallback);
    };
    if (name == "RISK_SESSION_INITIALIZED") {
      recovered.date = payload.Find("session_id") == nullptr
                           ? ""
                           : payload.Find("session_id")->as_string();
      recovered.policy_version =
          payload.Find("policy_version") == nullptr
              ? recovered.policy_version
              : payload.Find("policy_version")->as_string();
      starting = paise("starting_equity_paise", starting);
      current = paise("current_equity_paise", current);
      peak = paise("peak_equity_paise", peak);
      low = paise("low_equity_paise", low);
      recovered.halted = false;
      recovered.halt_reason.clear();
    } else if (name == "EQUITY_MARKED" || name == "DAILY_LIMIT_APPROACHED") {
      current = paise("current_equity_paise", current);
      peak = paise("peak_equity_paise", peak);
      low = paise("low_equity_paise", low);
    } else if (name == "KILL_SWITCH_ACTIVATED") {
      current = paise("current_equity_paise", current);
      low = paise("low_equity_paise", low);
      recovered.halted = true;
      const Json* reason = payload.Find("halt_reason");
      recovered.halt_reason =
          reason == nullptr ? "daily loss limit" : reason->as_string();
    } else {
      if (error)
        *error = "unknown risk event in journal";
      return false;
    }
    recovered.journal_sequence = event.sequence;
    applied_risk_event = true;
  }
  const bool has_risk_history = have_snapshot || applied_risk_event;
  recovered.starting_equity = Money::FromPaise(starting);
  recovered.current_equity = Money::FromPaise(current);
  recovered.peak_equity = Money::FromPaise(peak);
  recovered.low_equity = Money::FromPaise(low);
  recovered.realized_pnl = recovered.realized_pnl.valid()
                               ? recovered.realized_pnl
                               : Money::FromPaise(0);
  recovered.unrealized_pnl = recovered.unrealized_pnl.valid()
                                 ? recovered.unrealized_pnl
                                 : Money::FromPaise(0);
  if (!recovered.starting_equity.valid() ||
      (has_risk_history && starting <= 0) ||
      !recovered.current_equity.valid() || !recovered.peak_equity.valid() ||
      !recovered.low_equity.valid() ||
      recovered.low_equity > recovered.current_equity ||
      recovered.peak_equity < recovered.current_equity ||
      !recovered.realized_pnl.valid() || !recovered.unrealized_pnl.valid()) {
    if (error)
      *error = "reconstructed risk state is inconsistent";
    return false;
  }
  *state = recovered;
  if (error)
    error->clear();
  return true;
}

// ---------------------------------------------------------------------------
// Portfolio accounting.
// ---------------------------------------------------------------------------
Portfolio::Transaction::Transaction(Portfolio* owner)
    : owner_(owner),
      prior_ledger_(owner->ledger_),
      prior_positions_(owner->positions_) {
}

Portfolio::Transaction::~Transaction() {
  if (!committed_ && owner_ != nullptr) {
    owner_->ledger_ = prior_ledger_;
    owner_->positions_ = prior_positions_;
  }
}

bool Portfolio::Transaction::Commit(std::string* error) {
  if (!owner_->ValidateInvariants(error))
    return false;
  owner_->UpdatePeakEquity();
  committed_ = true;
  return true;
}

bool Portfolio::ValidateInvariants(std::string* error) const {
  auto bad = [error](const char* message) {
    if (error)
      *error = message;
    return false;
  };
  if (!ledger_.initial_capital.valid() || !ledger_.cash.valid() ||
      !ledger_.invested.valid() || !ledger_.realized_pnl.valid() ||
      !ledger_.unrealized_pnl.valid() || !ledger_.brokerage.valid() ||
      !ledger_.statutory.valid() || !ledger_.slippage.valid() ||
      !ledger_.operating.valid() || !ledger_.estimated_tax.valid() ||
      !ledger_.peak_equity.valid() || !ledger_.external_flows.valid())
    return bad("portfolio ledger contains an invalid monetary value");
  int64_t cash = 0;
  int64_t invested = 0;
  int64_t unrealized = 0;
  int64_t peak = 0;
  if (!ledger_.cash.TryPaise(&cash) || cash < 0)
    return bad("portfolio cash is negative or unavailable");
  if (!ledger_.invested.TryPaise(&invested) || invested < 0)
    return bad("cost basis is negative or unavailable");
  if (!ledger_.unrealized_pnl.TryPaise(&unrealized))
    return bad("unrealized P&L is unavailable");
  if (!ledger_.peak_equity.TryPaise(&peak) || peak <= 0)
    return bad("peak equity must be positive");
  if (ledger_.trade_count < 0)
    return bad("trade count is negative");
  int64_t expected_unrealized = 0;
  for (const auto& [symbol, position] : positions_) {
    (void)symbol;
    if (position.quantity <= 0)
      return bad("position quantity must be positive");
    int64_t last = 0;
    int64_t average = 0;
    int64_t contribution = 0;
    if (!position.last_price.TryPaise(&last) || last <= 0 ||
        !position.avg_price.TryPaise(&average) || average <= 0 ||
        !money_detail::CheckedSubtract(last, average, &contribution) ||
        !money_detail::CheckedMultiply(contribution,
                                       static_cast<int64_t>(position.quantity),
                                       &contribution) ||
        !money_detail::CheckedAdd(
            expected_unrealized, contribution, &expected_unrealized))
      return bad("position valuation overflow or invalid price");
  }
  if (expected_unrealized != unrealized)
    return bad("unrealized P&L does not reconcile to open positions");
  if (!equity().valid() || !market_value().valid() || !drawdown().valid())
    return bad("equity, market value or drawdown is invalid");
  return true;
}

Portfolio::Portfolio() : Portfolio(Money::FromRupees(0.0)) {
}

Portfolio::Portfolio(Money initial_capital) {
  ledger_.initial_capital = initial_capital;
  ledger_.cash = initial_capital;
  ledger_.invested = Money::FromPaise(0);
  ledger_.realized_pnl = Money::FromPaise(0);
  ledger_.unrealized_pnl = Money::FromPaise(0);
  ledger_.brokerage = Money::FromPaise(0);
  ledger_.statutory = Money::FromPaise(0);
  ledger_.slippage = Money::FromPaise(0);
  ledger_.operating = Money::FromPaise(0);
  ledger_.estimated_tax = Money::FromPaise(0);
  ledger_.peak_equity = initial_capital;
  ledger_.external_flows = Money::FromPaise(0);
  int64_t opening = 0;
  if (initial_capital.TryPaise(&opening))
    audit_.push_back("INITIAL_CAPITAL " + std::to_string(opening));
}

void Portfolio::UpdatePeakEquity() {
  const Money current = equity();
  if (current.valid() && current > ledger_.peak_equity)
    ledger_.peak_equity = current;
}

bool Portfolio::RecomputeUnrealized() {
  int64_t unrealized = 0;
  for (const auto& [symbol, position] : positions_) {
    (void)symbol;
    int64_t last = 0;
    int64_t average = 0;
    int64_t difference = 0;
    int64_t contribution = 0;
    if (!position.last_price.TryPaise(&last) ||
        !position.avg_price.TryPaise(&average) ||
        !money_detail::CheckedSubtract(last, average, &difference) ||
        !money_detail::CheckedMultiply(difference,
                                       static_cast<int64_t>(position.quantity),
                                       &contribution) ||
        !money_detail::CheckedAdd(unrealized, contribution, &unrealized))
      return false;
  }
  ledger_.unrealized_pnl = Money::FromPaise(unrealized);
  return true;
}

Money Portfolio::market_value() const {
  return ledger_.invested + ledger_.unrealized_pnl;
}

bool Portfolio::Buy(const std::string& symbol,
                    int quantity,
                    Money price,
                    const CostBreakdown& costs,
                    std::string* error,
                    const std::string& audit_tag) {
  auto fail = [error](const char* message) {
    if (error)
      *error = message;
    return false;
  };
  if (quantity <= 0 || symbol.empty())
    return fail("invalid buy quantity or symbol");
  int64_t price_paise = 0;
  if (!PositivePaise(price, &price_paise))
    return fail("invalid buy price");
  if (!costs.usable())
    return fail("invalid or unrecognised cost calculation");

  int64_t notional_paise = 0;
  int64_t required_paise = 0;
  int64_t next_quantity = 0;
  if (!money_detail::CheckedMultiply(
          price_paise, static_cast<int64_t>(quantity), &notional_paise))
    return fail("buy notional overflow");
  int64_t cost_paise = 0;
  if (!costs.total.TryPaise(&cost_paise) ||
      !money_detail::CheckedAdd(notional_paise, cost_paise, &required_paise))
    return fail("buy requirement overflow");
  const Money notional = Money::FromPaise(notional_paise);
  const Money required = Money::FromPaise(required_paise);
  if (!(required <= ledger_.cash))
    return fail("insufficient cash: required exceeds available");

  const auto it = positions_.find(symbol);
  if (it != positions_.end() &&
      (!money_detail::CheckedAdd(static_cast<int64_t>(it->second.quantity),
                                 static_cast<int64_t>(quantity),
                                 &next_quantity) ||
       next_quantity > std::numeric_limits<int>::max()))
    return fail("position quantity overflow");

  Transaction transaction(this);
  Money cash = Money::Invalid();
  Money invested = Money::Invalid();
  Money realized = Money::Invalid();
  Money brokerage = Money::Invalid();
  Money statutory = Money::Invalid();
  Money slippage = Money::Invalid();
  if (!CheckedMoneySubtract(ledger_.cash, required, &cash) ||
      !CheckedMoneyAdd(ledger_.invested, notional, &invested) ||
      !CheckedMoneySubtract(ledger_.realized_pnl, costs.total, &realized) ||
      !CheckedMoneyAdd(ledger_.brokerage, costs.brokerage, &brokerage) ||
      !CheckedMoneyAdd(ledger_.statutory,
                       costs.stt + costs.exchange_fee + costs.gst + costs.sebi +
                           costs.stamp + costs.dp,
                       &statutory) ||
      !CheckedMoneyAdd(ledger_.slippage, costs.slippage, &slippage))
    return fail("buy ledger arithmetic overflow");

  if (it == positions_.end()) {
    Position position;
    position.symbol = symbol;
    position.quantity = quantity;
    position.avg_price = price;
    position.last_price = price;
    positions_[symbol] = position;
  } else {
    int64_t left = 0;
    int64_t right = 0;
    int64_t weighted = 0;
    int64_t average = 0;
    if (!it->second.avg_price.TryPaise(&average) ||
        !money_detail::CheckedMultiply(
            average, static_cast<int64_t>(it->second.quantity), &left) ||
        !money_detail::CheckedMultiply(
            price_paise, static_cast<int64_t>(quantity), &right) ||
        !money_detail::CheckedAdd(left, right, &weighted))
      return fail("weighted cost overflow");
    it->second.avg_price = Money::FromPaise(weighted / next_quantity);
    it->second.quantity = static_cast<int>(next_quantity);
    it->second.last_price = price;
  }
  ledger_.cash = cash;
  ledger_.invested = invested;
  ledger_.realized_pnl = realized;
  ledger_.brokerage = brokerage;
  ledger_.statutory = statutory;
  ledger_.slippage = slippage;
  ++ledger_.trade_count;
  if (!RecomputeUnrealized())
    return fail("buy position-state overflow");
  if (!transaction.Commit(error))
    return false;
  audit_.push_back("BUY " + symbol + " qty=" + std::to_string(quantity) +
                   " price_paise=" + std::to_string(price_paise) +
                   " cost_paise=" + std::to_string(cost_paise));
  if (!audit_tag.empty())
    audit_.push_back("EXEC_TAG " + audit_tag);
  return true;
}

bool Portfolio::Sell(const std::string& symbol,
                     int quantity,
                     Money price,
                     const CostBreakdown& costs,
                     std::string* error,
                     const std::string& audit_tag) {
  auto fail = [error](const char* message) {
    if (error)
      *error = message;
    return false;
  };
  const auto it = positions_.find(symbol);
  if (it == positions_.end() || it->second.quantity < quantity || quantity <= 0)
    return fail("sell quantity exceeds position");
  int64_t price_paise = 0;
  if (!PositivePaise(price, &price_paise))
    return fail("invalid sell price");
  if (!costs.usable())
    return fail("invalid or unrecognised cost calculation");

  int64_t notional_paise = 0;
  int64_t basis_paise = 0;
  int64_t average = 0;
  int64_t cost_paise = 0;
  if (!it->second.avg_price.TryPaise(&average) ||
      !money_detail::CheckedMultiply(
          price_paise, static_cast<int64_t>(quantity), &notional_paise) ||
      !money_detail::CheckedMultiply(
          average, static_cast<int64_t>(quantity), &basis_paise) ||
      !costs.total.TryPaise(&cost_paise))
    return fail("sell notional overflow");
  const Money notional = Money::FromPaise(notional_paise);
  const Money basis = Money::FromPaise(basis_paise);
  Money proceeds = Money::Invalid();
  Money pnl = Money::Invalid();
  if (!CheckedMoneySubtract(notional, costs.total, &proceeds) ||
      !CheckedMoneySubtract(proceeds, basis, &pnl))
    return fail("sell proceeds arithmetic overflow");

  Transaction transaction(this);
  Money cash = Money::Invalid();
  Money invested = Money::Invalid();
  Money realized = Money::Invalid();
  Money brokerage = Money::Invalid();
  Money statutory = Money::Invalid();
  Money slippage = Money::Invalid();
  if (!CheckedMoneyAdd(ledger_.cash, proceeds, &cash) ||
      !CheckedMoneySubtract(ledger_.invested, basis, &invested) ||
      !CheckedMoneyAdd(ledger_.realized_pnl, pnl, &realized) ||
      !CheckedMoneyAdd(ledger_.brokerage, costs.brokerage, &brokerage) ||
      !CheckedMoneyAdd(ledger_.statutory,
                       costs.stt + costs.exchange_fee + costs.gst + costs.sebi +
                           costs.stamp + costs.dp,
                       &statutory) ||
      !CheckedMoneyAdd(ledger_.slippage, costs.slippage, &slippage))
    return fail("sell ledger arithmetic overflow");

  if (quantity == it->second.quantity) {
    positions_.erase(it);
  } else {
    it->second.quantity -= quantity;
    it->second.last_price = price;
  }
  ledger_.cash = cash;
  ledger_.invested = invested;
  ledger_.realized_pnl = realized;
  ledger_.brokerage = brokerage;
  ledger_.statutory = statutory;
  ledger_.slippage = slippage;
  ++ledger_.trade_count;
  if (!RecomputeUnrealized())
    return fail("sell position-state overflow");
  if (!transaction.Commit(error))
    return false;
  audit_.push_back("SELL " + symbol + " qty=" + std::to_string(quantity) +
                   " price_paise=" + std::to_string(price_paise) +
                   " pnl_paise=" + std::to_string(pnl.paise()));
  if (!audit_tag.empty())
    audit_.push_back("EXEC_TAG " + audit_tag);
  return true;
}

bool Portfolio::MarkToMarket(const std::string& symbol,
                             Money price,
                             std::string* error) {
  const auto it = positions_.find(symbol);
  if (it == positions_.end()) {
    if (error)
      *error = "mark-to-market unknown symbol";
    return false;
  }
  int64_t raw = 0;
  if (!PositivePaise(price, &raw)) {
    if (error)
      *error = "mark-to-market price invalid";
    return false;
  }
  Transaction transaction(this);
  it->second.last_price = price;
  if (!RecomputeUnrealized()) {
    if (error)
      *error = "mark-to-market overflow";
    return false;
  }
  return transaction.Commit(error);
}

bool Portfolio::RecordOperatingExpense(Money amount, std::string* error) {
  int64_t raw = 0;
  if (!amount.TryPaise(&raw) || raw < 0) {
    if (error)
      *error = "operating expense must be a valid non-negative amount";
    return false;
  }
  if (!(amount <= ledger_.cash)) {
    if (error)
      *error = "operating expense exceeds available cash";
    return false;
  }
  Transaction transaction(this);
  Money cash = Money::Invalid();
  Money operating = Money::Invalid();
  if (!CheckedMoneySubtract(ledger_.cash, amount, &cash) ||
      !CheckedMoneyAdd(ledger_.operating, amount, &operating)) {
    if (error)
      *error = "operating expense arithmetic overflow";
    return false;
  }
  ledger_.cash = cash;
  ledger_.operating = operating;
  if (!transaction.Commit(error))
    return false;
  audit_.push_back("OPERATING_EXPENSE " + std::to_string(raw));
  return true;
}

bool Portfolio::RecordExternalFlow(Money amount,
                                   const std::string& reason,
                                   std::string* error) {
  int64_t raw = 0;
  if (!amount.TryPaise(&raw) || reason.empty()) {
    if (error)
      *error = "external flow requires a valid amount and reason";
    return false;
  }
  Transaction transaction(this);
  Money cash = Money::Invalid();
  Money flows = Money::Invalid();
  if (!CheckedMoneyAdd(ledger_.cash, amount, &cash) ||
      !CheckedMoneyAdd(ledger_.external_flows, amount, &flows)) {
    if (error)
      *error = "external flow arithmetic overflow";
    return false;
  }
  ledger_.cash = cash;
  ledger_.external_flows = flows;
  if (!transaction.Commit(error))
    return false;
  audit_.push_back("RECONCILIATION_EXCEPTION " + std::to_string(raw) + " " +
                   reason);
  return true;
}

Money Portfolio::equity() const {
  return ledger_.cash + ledger_.invested + ledger_.unrealized_pnl;
}

Money Portfolio::available_cash() const {
  return ledger_.cash;
}

Money Portfolio::drawdown() const {
  const Money current = equity();
  if (!current.valid() || !ledger_.peak_equity.valid())
    return Money::Invalid();
  const Money loss = ledger_.peak_equity - current;
  return loss > Money::FromPaise(0) ? loss : Money::FromPaise(0);
}

double Portfolio::drawdown_fraction() const {
  int64_t peak = 0;
  int64_t loss = 0;
  if (!ledger_.peak_equity.TryPaise(&peak) || peak <= 0 ||
      !drawdown().TryPaise(&loss))
    return std::numeric_limits<double>::quiet_NaN();
  return static_cast<double>(loss) / static_cast<double>(peak);
}

// ---------------------------------------------------------------------------
// Shared position sizing. All consumers must use this function so simulated,
// paper and future execution environments allocate capital identically.
// ---------------------------------------------------------------------------
SizingResult SizePosition(const Config& config,
                          const Portfolio& portfolio,
                          const SizingInput& input) {
  SizingResult result;
  result.notional = Money::Invalid();
  auto reject = [&result](const char* reason) {
    result.quantity = 0;
    result.notional = Money::Invalid();
    result.reason = reason;
    return result;
  };

  int64_t price_paise = 0;
  if (!PositivePaise(input.price, &price_paise))
    return reject("invalid price");
  if (!std::isfinite(input.volatility) || input.volatility < 0.0)
    return reject("invalid volatility");
  if (!std::isfinite(input.stop_distance) || input.stop_distance < 0.0)
    return reject("invalid stop distance");
  if (!std::isfinite(input.tick_size) || input.tick_size < 0.0)
    return reject("invalid tick size");
  if (!std::isfinite(input.participation) || input.participation < 0.0 ||
      input.participation > 1.0)
    return reject("invalid participation");
  if (input.available_depth < 0)
    return reject("invalid depth");
  int64_t exposure_paise = 0;
  if (!input.current_exposure.TryPaise(&exposure_paise) || exposure_paise < 0)
    return reject("invalid current exposure");
  if (!std::isfinite(config.risk_per_trade_fraction) ||
      config.risk_per_trade_fraction <= 0.0 ||
      !std::isfinite(config.cash_reserve_fraction) ||
      config.cash_reserve_fraction < 0.0 ||
      !std::isfinite(config.max_gross_exposure_fraction) ||
      config.max_gross_exposure_fraction <= 0.0)
    return reject("invalid risk configuration");
  if (!std::isfinite(config.max_participation_fraction) ||
      config.max_participation_fraction <= 0.0 ||
      !std::isfinite(config.slippage_bps) || config.slippage_bps < 0.0)
    return reject("invalid execution configuration");

  const double price = input.price.rupees();
  if (input.tick_size > 0.0) {
    const double ticks = price / input.tick_size;
    if (!std::isfinite(ticks) || std::abs(ticks - std::round(ticks)) > 1e-6)
      return reject("price violates tick size");
  }

  int64_t equity_paise = 0;
  if (!portfolio.equity().TryPaise(&equity_paise) || equity_paise <= 0)
    return reject("non-positive equity");
  const double drawdown_fraction = portfolio.drawdown_fraction();
  if (!std::isfinite(drawdown_fraction))
    return reject("invalid drawdown");
  const double drawdown_scale = std::max(
      0.0,
      1.0 - drawdown_fraction / std::max(config.max_drawdown_fraction, 0.0001));
  if (!std::isfinite(drawdown_scale) || drawdown_scale <= 0.0)
    return reject("drawdown limit reached");

  const double risk_budget = static_cast<double>(equity_paise) / 100.0 *
                             config.risk_per_trade_fraction * drawdown_scale;
  const double stop_fraction = input.stop_distance > 0.0
                                   ? input.stop_distance
                                   : std::max(input.volatility, 0.005) * 2.0;
  const double risk_per_share = price * stop_fraction;
  if (!std::isfinite(risk_budget) || !std::isfinite(risk_per_share) ||
      risk_budget <= 0.0 || risk_per_share <= 0.0)
    return reject("invalid risk budget or stop distance");

  const Money reserve = Money::FromRupees(static_cast<double>(equity_paise) /
                                          100.0 * config.cash_reserve_fraction);
  Money spendable = Money::Invalid();
  if (!reserve.valid() ||
      !CheckedMoneySubtract(portfolio.available_cash(), reserve, &spendable))
    return reject("cash reserve arithmetic overflow");
  int64_t spendable_paise = 0;
  if (!spendable.TryPaise(&spendable_paise) || spendable_paise <= 0)
    return reject("insufficient cash after reserve");

  const Money exposure_cap =
      Money::FromRupees(static_cast<double>(equity_paise) / 100.0 *
                        config.max_gross_exposure_fraction);
  Money headroom = Money::Invalid();
  if (!exposure_cap.valid() ||
      !CheckedMoneySubtract(exposure_cap, input.current_exposure, &headroom))
    return reject("exposure arithmetic overflow");
  int64_t headroom_paise = 0;
  if (!headroom.TryPaise(&headroom_paise) || headroom_paise <= 0)
    return reject("exposure limit reached");

  if (input.available_depth <= 0 && !input.capacity_estimate)
    return reject("execution depth unavailable");
  const double participation = input.participation > 0.0
                                   ? input.participation
                                   : config.max_participation_fraction;
  const double depth_cap =
      input.available_depth > 0
          ? static_cast<double>(input.available_depth) * participation
          : std::numeric_limits<double>::infinity();

  const double raw_quantity = std::min(
      {risk_budget / risk_per_share,
       static_cast<double>(spendable_paise) / static_cast<double>(price_paise),
       static_cast<double>(headroom_paise) / static_cast<double>(price_paise),
       depth_cap});
  if (!std::isfinite(raw_quantity) || raw_quantity < 1.0)
    return reject("minimum one share violates risk/cash/liquidity budget");
  if (raw_quantity > static_cast<double>(std::numeric_limits<int>::max()))
    return reject("quantity conversion overflow");

  int quantity = static_cast<int>(std::floor(raw_quantity));
  Money approved_notional = Money::Invalid();
  while (quantity > 0) {
    int64_t notional_paise = 0;
    if (!money_detail::CheckedMultiply(
            price_paise, static_cast<int64_t>(quantity), &notional_paise))
      return reject("notional overflow");
    const Money notional = Money::FromPaise(notional_paise);
    const CostBreakdown costs =
        EstimateCosts(notional, input.delivery, true, config.slippage_bps);
    if (!costs.usable())
      return reject("invalid cost estimate");
    Money required = Money::Invalid();
    if (!CheckedMoneyAdd(notional, costs.total, &required))
      return reject("cash requirement overflow");
    if (required <= spendable) {
      approved_notional = notional;
      break;
    }
    --quantity;
  }
  if (quantity < 1 || !approved_notional.valid())
    return reject("minimum one share violates risk/cash/liquidity budget");
  result.quantity = quantity;
  result.notional = approved_notional;
  result.reason = "approved: current-equity sizing within all limits";
  return result;
}

// ---------------------------------------------------------------------------
// Deterministic event runtime.
// ---------------------------------------------------------------------------
namespace {

std::string EnvelopeDigest(const EventEnvelope& event) {
  std::ostringstream canonical;
  auto field = [&canonical](const std::string& value) {
    canonical << value.size() << ':' << value << '|';
  };
  field(event.event_id);
  field(event.partition_key);
  field(std::to_string(event.source_sequence));
  field(std::to_string(event.replay_offset));
  field(std::to_string(event.event_time_ms));
  field(std::to_string(event.receive_time_ms));
  field(std::to_string(static_cast<int>(event.type)));
  field(event.payload);
  return ChecksumHex(canonical.str());
}

}  // namespace

EventRuntime::EventRuntime(size_t capacity, QueuePolicy policy)
    : capacity_(capacity), policy_(policy) {
}

bool EventRuntime::Submit(const EventEnvelope& event, std::string* error) {
  if (event.event_id.empty() || event.partition_key.empty() ||
      event.payload.empty()) {
    result_.rejected += 1;
    if (error)
      *error = "event identity, partition and payload are required";
    return false;
  }
  const std::string digest = EnvelopeDigest(event);
  for (const EventEnvelope& queued : queue_) {
    if (queued.event_id != event.event_id)
      continue;
    if (EnvelopeDigest(queued) == digest) {
      result_.duplicates += 1;
      return true;
    }
    result_.conflicts += 1;
    result_.quarantined = true;
    if (error)
      *error = "queued event id reused with a different payload";
    return false;
  }
  const auto prior = digests_.find(event.event_id);
  if (prior != digests_.end()) {
    if (prior->second == digest) {
      result_.duplicates += 1;
      return true;  // Committed idempotent retry: no second transition.
    }
    result_.conflicts += 1;
    result_.quarantined = true;
    if (error)
      *error = "event id reused with a different payload";
    return false;
  }
  const auto committed = sequences_.find(event.partition_key);
  const auto reserved = accepted_.find(event.partition_key);
  const uint64_t committed_high = committed == sequences_.end()
                                      ? static_cast<uint64_t>(0)
                                      : committed->second;
  const uint64_t reserved_high =
      reserved == accepted_.end() ? static_cast<uint64_t>(0) : reserved->second;
  const uint64_t high = std::max(committed_high, reserved_high);
  if (event.source_sequence <= high) {
    result_.out_of_order += 1;
    result_.rejected += 1;
    if (error)
      *error = "source sequence is not ahead of accepted watermark";
    return false;
  }
  if (queue_.size() >= capacity_) {
    result_.rejected += 1;
    if (error)
      *error = policy_ == QueuePolicy::kRetry
                   ? "bounded queue full: caller must retry"
                   : "bounded queue full: event rejected";
    return false;
  }
  queue_.push_back(event);
  accepted_[event.partition_key] = event.source_sequence;
  result_.received += 1;
  return true;
}

bool EventRuntime::Abort(const std::string& event_id) {
  for (auto it = queue_.begin(); it != queue_.end(); ++it) {
    if (it->event_id != event_id)
      continue;
    const std::string partition = it->partition_key;
    queue_.erase(it);
    uint64_t highest = 0;
    const auto committed = sequences_.find(partition);
    if (committed != sequences_.end())
      highest = committed->second;
    for (const EventEnvelope& pending : queue_)
      if (pending.partition_key == partition)
        highest = std::max(highest, pending.source_sequence);
    accepted_[partition] = highest;
    return true;
  }
  return false;
}

bool EventRuntime::ProcessOne(std::string* error) {
  if (queue_.empty()) {
    if (error)
      *error = "event queue empty";
    return false;
  }
  // Peek: an event is not dequeued until its commit succeeds.
  const EventEnvelope event = queue_.front();
  const auto offset = offsets_.find(event.partition_key);
  const uint64_t committed_offset =
      offset == offsets_.end() ? static_cast<uint64_t>(0) : offset->second;
  if (event.replay_offset < committed_offset) {
    result_.offset_failures += 1;
    result_.quarantined = true;
    if (error)
      *error = "replay offset moved backwards; event retained in queue";
    return false;
  }
  const auto current = sequences_.find(event.partition_key);
  if (current != sequences_.end() && event.source_sequence <= current->second) {
    result_.offset_failures += 1;
    if (error)
      *error = "committed sequence watermark would regress";
    return false;
  }
  ++applied_;  // Reducer step succeeded for this deterministic prototype.
  const std::string digest = EnvelopeDigest(event);
  digests_[event.event_id] = digest;
  sequences_[event.partition_key] = event.source_sequence;
  offsets_[event.partition_key] =
      std::max(committed_offset, event.replay_offset);
  result_.committed += 1;
  queue_.pop_front();
  return true;
}

RuntimeResult EventRuntime::Drain(std::string* error) {
  while (!queue_.empty()) {
    if (!ProcessOne(error))
      break;
  }
  return result_;
}

// ---------------------------------------------------------------------------
// Central risk engine.
// ---------------------------------------------------------------------------
RiskEngine::RiskEngine(Config config)
    : config_(std::move(config)), state_(std::make_shared<DailyRiskState>()) {
}

RiskEngine::RiskEngine(Config config, std::shared_ptr<DailyRiskState> state)
    : config_(std::move(config)), state_(std::move(state)) {
}

RiskEngine::RiskEngine(Config config,
                       std::shared_ptr<DailyRiskState> state,
                       Journal* journal)
    : config_(std::move(config)), state_(std::move(state)), journal_(journal) {
}

RiskDecision RiskEngine::Evaluate(const Signal& signal,
                                  const Quote& quote,
                                  const Portfolio& portfolio,
                                  int64_t now_ms,
                                  OrderSide side) {
  std::lock_guard<std::recursive_mutex> guard(*authority_lock_);
  RiskDecision decision;
  decision.notional = Money::FromRupees(0.0);
  auto reject = [&decision](const std::string& reason) {
    decision.approved = false;
    decision.quantity = 0;
    decision.reason = reason;
    return decision;
  };

  if (signal.symbol.empty() || quote.symbol != signal.symbol ||
      signal.permanent_id.empty() || quote.permanent_id.empty() ||
      signal.permanent_id != quote.permanent_id || signal.venue.empty() ||
      quote.venue.empty() || signal.venue != quote.venue ||
      signal.listing_id.empty() || quote.listing_id.empty() ||
      signal.listing_id != quote.listing_id)
    return reject("signal and quote identity do not match");
  const std::string session = UtcSessionDate(now_ms);
  const Money equity = portfolio.equity();
  if (!equity.valid()) {
    state_->halted = true;
    state_->halt_reason = "invalid portfolio equity";
    return reject(state_->halt_reason);
  }
  auto persist = [&](const char* action) {
    if (journal_ == nullptr)
      return true;
    std::string risk_error;
    if (!AppendRiskEvent(journal_, action, state_.get(), now_ms, &risk_error)) {
      decision.reason =
          std::string("risk event persistence failed: ") + risk_error;
      return false;
    }
    return true;
  };
  if (state_->date != session) {
    state_->date = session;
    state_->starting_equity = equity;
    state_->current_equity = equity;
    state_->peak_equity = equity;
    state_->low_equity = equity;
    state_->realized_pnl = portfolio.ledger().realized_pnl;
    state_->unrealized_pnl = portfolio.ledger().unrealized_pnl;
    state_->halted = false;
    state_->halt_reason.clear();
    if (!persist("RISK_SESSION_INITIALIZED")) {
      decision.approved = false;
      decision.quantity = 0;
      return decision;
    }
  } else {
    bool changed = false;
    if (equity != state_->current_equity) {
      state_->current_equity = equity;
      changed = true;
    }
    if (equity > state_->peak_equity) {
      state_->peak_equity = equity;
      changed = true;
    }
    if (equity < state_->low_equity) {
      state_->low_equity = equity;
      changed = true;
    }
    state_->realized_pnl = portfolio.ledger().realized_pnl;
    state_->unrealized_pnl = portfolio.ledger().unrealized_pnl;
    if (changed && !persist("EQUITY_MARKED")) {
      decision.approved = false;
      decision.quantity = 0;
      return decision;
    }
  }
  if (state_->halted)
    return reject("daily kill switch active: " + state_->halt_reason);
  int64_t start = 0;
  int64_t low = 0;
  if (!state_->starting_equity.TryPaise(&start) ||
      !state_->low_equity.TryPaise(&low) || start <= 0)
    return reject("invalid risk baseline");
  const double loss_fraction =
      static_cast<double>(start - low) / static_cast<double>(start);
  if (loss_fraction >= config_.max_daily_loss_fraction) {
    state_->halted = true;
    state_->halt_reason = "daily loss limit reached";
    if (!persist("KILL_SWITCH_ACTIVATED")) {
      decision.approved = false;
      decision.quantity = 0;
      return decision;
    }
    return reject("daily loss limit reached");
  }

  if (!signal.tradable)
    return reject("signal rejected upstream: " + signal.rejection_reason);
  if (config_.live_trading_enabled && config_.mode != Mode::kLive) {
    return reject("fail-closed: live flag inconsistent with execution mode");
  }
  if (ProviderFreshnessUnknown(quote)) {
    return reject("provider freshness unknown");
  }
  if (IsStaleProviderUpdate(quote, now_ms, config_.stale_quote_seconds)) {
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

  SizingInput sizing;
  sizing.side = side;
  sizing.price = Money::FromRupees(quote.ask);  // Executable ask for a buy.
  sizing.volatility = signal.features.vol20;
  sizing.stop_distance = config_.stop_loss_fraction;
  sizing.delivery = config_.execution_delivery;
  sizing.available_depth =
      side == OrderSide::kBuy ? quote.ask_qty : quote.bid_qty;
  sizing.current_exposure = portfolio.market_value();
  if (sizing.available_depth <= 0)
    return reject("missing executable ask depth");
  const SizingResult sized = SizePosition(config_, portfolio, sizing);
  if (sized.quantity < 1)
    return reject(sized.reason);

  decision.approved = true;
  decision.quantity = sized.quantity;
  decision.notional = sized.notional;
  decision.reason = sized.reason;
  return decision;
}

// Secret salt for authorization binding. Kept in the implementation unit so
// a caller cannot construct a matching signature from public headers.
namespace {
constexpr const char* kAuthorizationSalt =
    "QUANT-RISK-AUTH-V2-2026-11-26-CAPABILITY-9f2c71ad";

}  // namespace

std::string RiskAuthorization::ComputeSignature() const {
  std::string canonical;
  auto add = [&canonical](const std::string& field) {
    canonical += field;
    canonical.push_back('\n');
  };
  add("QUANT-RISK-AUTHORIZATION-V2");
  add(kAuthorizationSalt);
  add(order_id_);
  add(account_id_);
  add(instrument_id_);
  add(venue_);
  add(listing_id_);
  add(std::to_string(static_cast<int>(market_data_source_)));
  add(std::to_string(market_data_version_));
  add(symbol_);
  add(side_ == OrderSide::kBuy ? "BUY" : "SELL");
  add(std::to_string(quantity_));
  add(std::to_string(limit_price_paise_));
  add(std::to_string(max_notional_paise_));
  add(policy_version_);
  add(std::to_string(state_revision_));
  add(std::to_string(expiry_ms_));
  return Sha256Hex(canonical);
}

bool RiskAuthorization::Verify(const PaperOrder& order,
                               const Config& config,
                               int64_t now_ms,
                               std::string* error) const {
  auto fail = [error](const char* reason) {
    if (error)
      *error = reason;
    return false;
  };
  if (!valid_ || signature_.empty())
    return fail("risk authorization is not valid");
  if (order.id != order_id_ || order.symbol != symbol_ || order.side != side_ ||
      order.quantity != quantity_ ||
      order.limit_price.paise() != limit_price_paise_ ||
      order.instrument_key != instrument_id_ || order.venue != venue_ ||
      order.listing_id != listing_id_)
    return fail("risk authorization is not bound to this order");
  if (config.account_id != account_id_)
    return fail("risk authorization account mismatch");
  if (now_ms > expiry_ms_ || now_ms <= 0)
    return fail("risk authorization expired or has invalid time");
  int64_t expected = 0;
  if (!money_detail::CheckedMultiply(
          limit_price_paise_, static_cast<int64_t>(quantity_), &expected) ||
      expected > max_notional_paise_)
    return fail("risk authorization notional does not cover the order");
  if (ComputeSignature() != signature_)
    return fail("risk authorization signature mismatch");
  return true;
}

bool RiskAuthorization::VerifyMarketDataSnapshot(const Quote& quote,
                                                 std::string* error) const {
  auto fail = [error](const char* reason) {
    if (error)
      *error = reason;
    return false;
  };
  if (!valid_)
    return fail("risk authorization is not valid");
  if (quote.symbol != symbol_ || quote.permanent_id != instrument_id_ ||
      quote.venue != venue_ || quote.listing_id != listing_id_ ||
      quote.source != market_data_source_ ||
      quote.sequence != market_data_version_)
    return fail("market snapshot does not match authorized data version");
  return true;
}

bool RiskAuthorization::VerifyRiskState(const DailyRiskState& state,
                                        int64_t now_ms,
                                        std::string* error) const {
  auto fail = [error](const char* reason) {
    if (error)
      *error = reason;
    return false;
  };
  if (!valid_)
    return fail("risk authorization is not valid");
  if (now_ms > expiry_ms_ || now_ms <= 0)
    return fail("risk authorization expired or has invalid time");
  if (state.halted)
    return fail("risk kill switch active: authorization revoked");
  if (state.policy_version != policy_version_)
    return fail("risk policy changed: authorization revoked");
  if (state.journal_sequence != state_revision_)
    return fail("risk state revision changed: authorization revoked");
  return true;
}

RiskAuthorization RiskEngine::Authorize(const Signal& signal,
                                        const Quote& quote,
                                        const Portfolio& portfolio,
                                        const PaperOrder& order,
                                        int64_t now_ms) {
  RiskAuthorization auth;
  std::lock_guard<std::recursive_mutex> guard(*authority_lock_);
  if (signal.symbol.empty() || quote.symbol.empty() || order.symbol.empty() ||
      signal.symbol != quote.symbol || signal.symbol != order.symbol ||
      signal.permanent_id.empty() || quote.permanent_id.empty() ||
      order.instrument_key.empty() ||
      signal.permanent_id != quote.permanent_id ||
      signal.permanent_id != order.instrument_key || signal.venue.empty() ||
      quote.venue.empty() || order.venue.empty() ||
      signal.venue != quote.venue || signal.venue != order.venue ||
      signal.listing_id.empty() || quote.listing_id.empty() ||
      order.listing_id.empty() || signal.listing_id != quote.listing_id ||
      signal.listing_id != order.listing_id)
    return auth;
  const RiskDecision decision =
      Evaluate(signal, quote, portfolio, now_ms, order.side);
  if (!decision.approved || decision.quantity < order.quantity ||
      !decision.notional.valid())
    return auth;  // Invalid capability: no authority granted.
  auth.order_id_ = order.id;
  auth.account_id_ = config_.account_id;
  auth.instrument_id_ = order.instrument_key;
  auth.venue_ = order.venue;
  auth.listing_id_ = order.listing_id;
  auth.market_data_source_ = quote.source;
  auth.market_data_version_ = quote.sequence;
  auth.symbol_ = order.symbol;
  auth.side_ = order.side;
  auth.quantity_ = order.quantity;
  auth.limit_price_paise_ = order.limit_price.paise();
  int64_t notional = 0;
  if (!decision.notional.TryPaise(&notional) || notional <= 0)
    return auth;
  auth.max_notional_paise_ = notional;
  auth.policy_version_ = state_->policy_version;
  auth.state_revision_ = state_->journal_sequence;
  constexpr int64_t kAuthorizationTtlMs = 5 * 60 * 1000;
  if (!money_detail::CheckedAdd(now_ms, kAuthorizationTtlMs, &auth.expiry_ms_))
    return auth;
  auth.signature_ = auth.ComputeSignature();
  auth.valid_ = true;
  return auth;
}

// ---------------------------------------------------------------------------
// Features and strategies.
// ---------------------------------------------------------------------------
FeatureSet ComputeFeatures(const std::vector<Candle>& candles) {
  FeatureSet features;
  if (candles.size() < 21)
    return features;
  auto return_over = [&candles](size_t periods) {
    if (candles.size() <= periods)
      return 0.0;
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
  if (candles.back().close > sma20)
    ++bullish;
  if (candles.size() >= 60) {
    ++checks;
    if (sma20 > sma60)
      ++bullish;
  }
  if (candles.size() >= 120) {
    ++checks;
    if (sma20 > sma120)
      ++bullish;
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
      range > 0.0
          ? Clamp(
                (candles.back().close - lowest) / range * 2.0 - 1.0, -1.0, 1.0)
          : 0.0;

  double weighted_typical = 0.0;
  double total_volume = 0.0;
  for (size_t i = candles.size() - 20; i < candles.size(); ++i) {
    const double typical =
        (candles[i].high + candles[i].low + candles[i].close) / 3.0;
    weighted_typical += typical * candles[i].volume;
    total_volume += candles[i].volume;
  }
  const double daily_vwap_proxy =
      total_volume > 0.0 ? weighted_typical / total_volume : 0.0;
  features.vwap_dev = daily_vwap_proxy > 0.0
                          ? candles.back().close / daily_vwap_proxy - 1.0
                          : 0.0;
  features.valid = true;
  return features;
}

double CrossSectionalMomentumScore(const FeatureSet& f) {
  if (!f.valid)
    return 0.0;
  const double vol_adjust = 1.0 / std::max(f.vol20, 0.005);
  const double raw =
      0.15 * f.ret5 + 0.25 * f.ret20 + 0.35 * f.ret60 + 0.25 * f.ret120;
  return Clamp(raw * vol_adjust * 0.05, -1.0, 1.0);
}

double TimeSeriesMomentumScore(const FeatureSet& f) {
  if (!f.valid)
    return 0.0;
  return Clamp((f.trend - 0.5) * 2.0 + f.breakout * 0.3, -1.0, 1.0);
}

double VolumeBreakoutScore(const FeatureSet& f) {
  if (!f.valid)
    return 0.0;
  return Clamp(f.breakout * 0.6 + Clamp(f.volume_surprise, -1.0, 2.0) * 0.2 +
                   Clamp(f.vwap_dev * 20.0, -1.0, 1.0) * 0.2,
               -1.0,
               1.0);
}

double RegimeScore(const std::vector<Candle>& index_candles) {
  if (index_candles.size() < 21)
    return 0.0;
  const FeatureSet f = ComputeFeatures(index_candles);
  const double vol_penalty = Clamp(0.02 / std::max(f.vol20, 0.002), 0.25, 1.5);
  return Clamp((0.6 * f.ret20 / std::max(f.vol20, 0.002) * 0.05 +
                0.4 * (f.trend - 0.5) * 2.0) *
                   vol_penalty,
               -1.0,
               1.0);
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
Money ConservativeStopFill(double entry_price,
                           const Candle& candle,
                           double stop_fraction) {
  const double stop_price = entry_price * (1.0 - stop_fraction);
  if (candle.low <= stop_price) {
    return Money::FromRupees(std::min(candle.open, stop_price));
  }
  return Money::FromRupees(candle.open);
}

size_t NextSessionIndex(const std::vector<Candle>& series,
                        const std::string& date) {
  for (size_t i = 0; i < series.size(); ++i) {
    if (series[i].date > date)
      return i;
  }
  return series.size();
}

double PriorAverageVolume(const std::vector<Candle>& series,
                          size_t index,
                          size_t window) {
  if (index == 0 || window == 0)
    return 0.0;
  const size_t start = index > window ? index - window : 0;
  double total = 0.0;
  for (size_t i = start; i < index; ++i)
    total += series[i].volume;
  return total / static_cast<double>(index - start);
}

BacktestMetrics RunBacktest(
    const std::map<std::string, std::vector<Candle>>& history,
    const Config& config) {
  BacktestMetrics metrics;
  if (config.max_open_positions != 1) {
    metrics.warnings.push_back(
        "multi-position backtest unsupported; configure MAX_OPEN_POSITIONS=1");
    return metrics;
  }
  metrics.gross_pnl = Money::FromRupees(0.0);
  metrics.net_pnl = Money::FromRupees(0.0);
  metrics.max_drawdown = Money::FromRupees(0.0);

  std::set<std::string> dates;
  for (const auto& [symbol, candles] : history) {
    (void)symbol;
    for (const Candle& candle : candles)
      dates.insert(candle.date);
  }
  if (dates.size() < 130)
    return metrics;

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
  auto record_gross = [&](Money price, Money basis, int quantity) {
    int64_t exit = 0;
    int64_t average = 0;
    int64_t difference = 0;
    int64_t gross = 0;
    int64_t wins = gross_wins;
    int64_t losses = gross_losses;
    if (!price.TryPaise(&exit) || !basis.TryPaise(&average) ||
        !money_detail::CheckedSubtract(exit, average, &difference) ||
        !money_detail::CheckedMultiply(
            difference, static_cast<int64_t>(quantity), &gross)) {
      metrics.halted = true;
      metrics.warnings.push_back("gross P&L arithmetic overflow");
      return false;
    }
    if (gross > 0) {
      if (!money_detail::CheckedAdd(wins, gross, &wins)) {
        metrics.halted = true;
        metrics.warnings.push_back("gross win accumulation overflow");
        return false;
      }
    } else if (gross < 0) {
      if (!money_detail::CheckedAdd(losses, -gross, &losses)) {
        metrics.halted = true;
        metrics.warnings.push_back("gross loss accumulation overflow");
        return false;
      }
    }
    gross_wins = wins;
    gross_losses = losses;
    metrics.gross_pnl += Money::FromPaise(gross);
    return metrics.gross_pnl.valid();
  };

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
          std::string mark_error;
          if (!portfolio.MarkToMarket(
                  open_symbol, Money::FromRupees(candle.close), &mark_error)) {
            metrics.warnings.push_back("mark failed: " + mark_error);
            metrics.halted = true;
            break;
          }
          const bool stop = candle.low <= entry_price.rupees() *
                                              (1.0 - config.stop_loss_fraction);
          if (current_index >=
                  entry_index + static_cast<size_t>(hold_sessions) ||
              stop) {
            const Money exit_price = ConservativeStopFill(
                entry_price.rupees(), candle, config.stop_loss_fraction);
            auto position = portfolio.positions().find(open_symbol);
            if (position != portfolio.positions().end()) {
              const int qty = position->second.quantity;
              int64_t exit_notional = 0;
              if (!money_detail::CheckedMultiply(exit_price.paise(),
                                                 static_cast<int64_t>(qty),
                                                 &exit_notional)) {
                metrics.warnings.push_back("exit notional overflow");
                metrics.halted = true;
                break;
              }
              const CostBreakdown costs =
                  EstimateCosts(Money::FromPaise(exit_notional),
                                config.backtest_delivery,
                                false,
                                config.slippage_bps);
              if (!costs.usable()) {
                metrics.warnings.push_back("exit cost calculation invalid");
                metrics.halted = true;
                break;
              }
              const Money basis = position->second.avg_price;  // per share.
              std::string sell_error;
              if (!portfolio.Sell(
                      open_symbol, qty, exit_price, costs, &sell_error)) {
                metrics.warnings.push_back("sell failed: " + sell_error);
                metrics.halted = true;
                break;
              }
              if (!record_gross(exit_price, basis, qty)) {
                ++metrics.trades;
                break;
              }
              ++metrics.trades;
              if (exit_price > basis)
                ++metrics.wins;
            }
            open_symbol.clear();
          }
        }
      }
    }

    std::vector<Candle> index_window;
    for (const Candle& candle : index) {
      if (candle.date <= decision_date)
        index_window.push_back(candle);
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
      if (symbol == "NIFTY 50" || symbol == "INDIA VIX")
        continue;
      std::vector<Candle> window;
      for (const Candle& candle : candles) {
        if (candle.date <= decision_date)
          window.push_back(candle);
      }
      if (window.size() < 130)
        continue;
      const FeatureSet features = ComputeFeatures(window);
      if (!features.valid)
        continue;
      const double score = EnsembleScore(features, regime);
      if (score > best_score) {
        best_score = score;
        best_symbol = symbol;
      }
    }

    if (!best_symbol.empty()) {
      auto series_it = history.find(best_symbol);
      const auto& series = series_it->second;
      const size_t execution_index = NextSessionIndex(series, decision_date);
      if (execution_index < series.size() &&
          series[execution_index].open > 0.0) {
        const Candle& execution_candle = series[execution_index];
        std::vector<Candle> window(series.begin(),
                                   series.begin() + execution_index);
        const FeatureSet features = ComputeFeatures(window);
        SizingInput sizing;
        sizing.price = Money::FromRupees(execution_candle.open);
        sizing.volatility = features.vol20;
        sizing.available_depth = static_cast<int64_t>(
            PriorAverageVolume(series, execution_index, 20));
        sizing.capacity_estimate = true;
        sizing.stop_distance = config.stop_loss_fraction;
        sizing.delivery = config.backtest_delivery;
        const SizingResult sized = SizePosition(config, portfolio, sizing);
        if (sized.quantity >= 1) {
          const Money price = Money::FromRupees(execution_candle.open);
          const CostBreakdown costs = EstimateCosts(sized.notional,
                                                    config.backtest_delivery,
                                                    true,
                                                    config.slippage_bps);
          if (!costs.usable()) {
            metrics.warnings.push_back("entry cost calculation invalid");
            metrics.halted = true;
            break;
          }
          std::string error;
          if (portfolio.Buy(
                  best_symbol, sized.quantity, price, costs, &error)) {
            open_symbol = best_symbol;
            entry_index = execution_index;
            entry_price = price;
            hold_sessions = 3;
          }
        }
      }
    }

    metrics.max_drawdown = std::max(metrics.max_drawdown, portfolio.drawdown());
    equity_curve.push_back(portfolio.equity().rupees());
  }

  metrics.terminal_liquidation_requested = config.terminal_liquidation;
  if (config.terminal_liquidation) {
    std::vector<std::pair<std::string, int>> terminal_positions;
    for (const auto& [symbol, position] : portfolio.positions())
      terminal_positions.emplace_back(symbol, position.quantity);
    for (const auto& [symbol, qty] : terminal_positions) {
      auto series_it = history.find(symbol);
      const Money price =
          series_it == history.end() || series_it->second.empty()
              ? Money::Invalid()
              : Money::FromRupees(series_it->second.back().close);
      if (!price.valid()) {
        metrics.warnings.push_back("terminal liquidation missing price for " +
                                   symbol);
        metrics.terminal_liquidation_failed = true;
        metrics.halted = true;
        continue;
      }
      int64_t notional_paise = 0;
      if (!money_detail::CheckedMultiply(
              price.paise(), static_cast<int64_t>(qty), &notional_paise)) {
        metrics.warnings.push_back("terminal notional overflow for " + symbol);
        metrics.terminal_liquidation_failed = true;
        metrics.halted = true;
        continue;
      }
      const CostBreakdown costs =
          EstimateCosts(Money::FromPaise(notional_paise),
                        config.backtest_delivery,
                        false,
                        config.slippage_bps);
      if (!costs.usable()) {
        metrics.warnings.push_back("terminal cost estimate invalid for " +
                                   symbol);
        metrics.terminal_liquidation_failed = true;
        metrics.halted = true;
        continue;
      }
      const auto position_it = portfolio.positions().find(symbol);
      if (position_it == portfolio.positions().end())
        continue;
      const Money basis = position_it->second.avg_price;
      std::string terminal_error;
      if (!portfolio.Sell(symbol, qty, price, costs, &terminal_error)) {
        metrics.warnings.push_back("terminal liquidation failed: " +
                                   terminal_error);
        metrics.terminal_liquidation_failed = true;
        metrics.halted = true;
        continue;
      }
      if (!record_gross(price, basis, qty)) {
        metrics.terminal_liquidation_failed = true;
        break;
      }
      ++metrics.trades;
      if (price > basis)
        ++metrics.wins;
    }
    metrics.terminal_open_positions = portfolio.open_position_count();
    metrics.terminal_liquidation_completed =
        !metrics.terminal_liquidation_failed &&
        metrics.terminal_open_positions == 0;
    if (!metrics.terminal_liquidation_completed && !metrics.halted) {
      metrics.warnings.push_back(
          "terminal liquidation incomplete with residual positions");
      metrics.halted = true;
    }
  } else {
    std::vector<std::string> symbols;
    for (const auto& [symbol, position] : portfolio.positions()) {
      (void)position;
      symbols.push_back(symbol);
    }
    for (const std::string& symbol : symbols) {
      auto series_it = history.find(symbol);
      const Money price =
          series_it == history.end() || series_it->second.empty()
              ? Money::Invalid()
              : Money::FromRupees(series_it->second.back().close);
      std::string error;
      if (!price.valid() || !portfolio.MarkToMarket(symbol, price, &error)) {
        metrics.warnings.push_back("terminal mark failed: " + symbol + " " +
                                   error);
        metrics.halted = true;
      }
    }
    metrics.terminal_open_positions = portfolio.open_position_count();
  }
  // Terminal valuation is part of the equity path used for drawdown.
  metrics.max_drawdown = std::max(metrics.max_drawdown, portfolio.drawdown());
  equity_curve.push_back(portfolio.equity().rupees());

  metrics.net_pnl = portfolio.equity() - config.initial_capital;
  metrics.realized_pnl = portfolio.ledger().realized_pnl;
  metrics.unrealized_pnl = portfolio.ledger().unrealized_pnl;
  metrics.brokerage = portfolio.ledger().brokerage;
  metrics.statutory = portfolio.ledger().statutory;
  metrics.slippage = portfolio.ledger().slippage;
  metrics.open_positions = portfolio.open_position_count();
  metrics.audit = portfolio.audit();
  metrics.final_equity = portfolio.equity().rupees();
  if (gross_losses > 0) {
    metrics.profit_factor =
        static_cast<double>(gross_wins) / static_cast<double>(gross_losses);
    metrics.profit_factor_status = "GROSS";
  } else {
    metrics.profit_factor = 0.0;
    metrics.profit_factor_status = "NOT_MEANINGFUL";
  }
  const double gross = std::abs(metrics.gross_pnl.rupees());
  if (gross > 0.0) {
    metrics.cost_to_gross = std::abs(portfolio.ledger().brokerage.rupees() +
                                     portfolio.ledger().statutory.rupees() +
                                     portfolio.ledger().slippage.rupees()) /
                            gross;
    metrics.cost_to_gross_status = "MEANINGFUL";
  } else {
    metrics.cost_to_gross = 0.0;
    metrics.cost_to_gross_status = "NOT_MEANINGFUL";
  }
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
    if (value < 0.0)
      downside += value * value;
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
                                      uint64_t seed,
                                      size_t draws) {
  if (returns.empty() || draws == 0)
    return {0.0, 0.0, 0.0};
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
  if (observations < 3)
    return 0.0;
  const double denominator = std::log(static_cast<double>(observations) + 1.0);
  if (denominator <= 0.0)
    return 0.0;
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

namespace {

constexpr int32_t kSupportedSchemaVersion = 1;
constexpr size_t kMaxEventPayloadBytes = 1u << 20;

// The single authoritative event contract. Append and Replay both call this
// validator, so no event can be acknowledged that the reader would reject.
bool ValidateEventFields(const Event& event, std::string* error) {
  auto reject = [error](const char* message) {
    if (error)
      *error = message;
    return false;
  };
  const auto type = static_cast<int32_t>(event.type);
  if (type < 0 || type > static_cast<int32_t>(EventType::kHeartbeat))
    return reject("invalid event type");
  if (event.schema_version != kSupportedSchemaVersion)
    return reject("unsupported schema version");
  if (event.id.empty())
    return reject("empty event id");
  if (event.id.find_first_of("|\r\n") != std::string::npos)
    return reject("unescaped delimiter in event id");
  if (event.timestamp_ms <= 0)
    return reject("invalid event timestamp");
  if (event.payload.size() > kMaxEventPayloadBytes)
    return reject("event payload exceeds limit");
  if (event.payload.find('\r') != std::string::npos ||
      event.payload.find('\n') != std::string::npos)
    return reject("event payload contains a record terminator");
  return true;
}

}  // namespace

std::string EventId(uint64_t sequence, std::string_view payload) {
  std::ostringstream oss;
  oss << "evt-" << std::setfill('0') << std::setw(12) << sequence << "-"
      << std::hex << Checksum(payload);
  return oss.str();
}

bool Journal::Open(const std::string& path, std::string* error) {
  if (healthy_ || locked_ || lock_fd_ >= 0) {
    if (error)
      *error = "journal already open";
    return false;
  }
  path_ = path;
  healthy_ = false;
  std::string directory = ".";
  const size_t slash = path_.find_last_of('/');
  if (slash != std::string::npos) {
    directory = path_.substr(0, slash);
    std::filesystem::create_directories(directory);
  }
  lock_fd_ =
      open((path_ + ".lock").c_str(), O_CREAT | O_RDWR | O_CLOEXEC, 0600);
  if (lock_fd_ < 0 || flock(lock_fd_, LOCK_EX | LOCK_NB) != 0) {
    if (lock_fd_ >= 0)
      close(lock_fd_);
    lock_fd_ = -1;
    locked_ = false;
    if (error)
      *error = "journal writer lock unavailable";
    return false;
  }
  locked_ = true;
  writer_thread_ = std::this_thread::get_id();
  auto release = [&]() {
    flock(lock_fd_, LOCK_UN);
    close(lock_fd_);
    lock_fd_ = -1;
    locked_ = false;
    writer_thread_ = std::thread::id();
    healthy_ = false;
  };
  // Create the journal file explicitly. A file created later by Append would
  // only be file-synced, leaving its directory entry undurable on first
  // creation. Establish the entry before the journal is declared healthy.
  const bool existed = std::filesystem::exists(path_);
  const int file_fd = open(path_.c_str(),
                           (existed ? O_WRONLY : O_WRONLY | O_CREAT | O_EXCL) |
                               O_CLOEXEC | O_NOFOLLOW,
                           0600);
  if (file_fd < 0) {
    release();
    if (error)
      *error = "cannot open journal file";
    return false;
  }
  int file_error = 0;
  struct stat status{};
  if (fstat(file_fd, &status) != 0 ||
      (((status.st_mode & 0777) != 0600) && fchmod(file_fd, 0600) != 0))
    file_error = errno;
  if (file_error == 0 && fsync(file_fd) != 0)
    file_error = errno;
  if (close(file_fd) != 0 && file_error == 0)
    file_error = errno;
  if (file_error != 0) {
    release();
    if (error)
      *error = "journal file initialization failed";
    return false;
  }
  if (!existed) {
    const int dir_fd =
        open(directory.c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (dir_fd < 0 || fsync(dir_fd) != 0) {
      if (dir_fd >= 0)
        close(dir_fd);
      release();
      if (error)
        *error = "journal directory synchronization failed";
      return false;
    }
    close(dir_fd);
  }
  if (std::filesystem::exists(path_)) {
    std::string replay_error;
    const std::vector<Event> existing = Replay(&replay_error);
    if (!replay_error.empty()) {
      if (error)
        *error = "journal validation failed: " + replay_error;
      flock(lock_fd_, LOCK_UN);
      close(lock_fd_);
      lock_fd_ = -1;
      locked_ = false;
      writer_thread_ = std::thread::id();
      return false;
    }
    size_ = existing.size();
    next_sequence_ = existing.empty() ? 1 : existing.back().sequence + 1;
  } else {
    size_ = 0;
    next_sequence_ = 1;
  }
  healthy_ = true;
  if (error)
    error->clear();
  return true;
}

void Journal::Close() {
  if (lock_fd_ >= 0) {
    flock(lock_fd_, LOCK_UN);
    close(lock_fd_);
    lock_fd_ = -1;
  }
  locked_ = false;
  healthy_ = false;
  writer_thread_ = std::thread::id();
}

Journal::~Journal() {
  if (lock_fd_ >= 0) {
    flock(lock_fd_, LOCK_UN);
    close(lock_fd_);
  }
}

bool Journal::Append(Event* event, std::string* error) {
  if (!healthy_ || !locked_ || lock_fd_ < 0) {
    if (error)
      *error = "journal unhealthy or writer lock not held";
    return false;
  }
  if (std::this_thread::get_id() != writer_thread_) {
    if (error)
      *error = "journal writer ownership is thread-local";
    return false;
  }
  if (event == nullptr) {
    if (error)
      *error = "journal event is null";
    return false;
  }
  if (path_.empty()) {
    if (error)
      *error = "journal not opened";
    return false;
  }
  if (next_sequence_ == std::numeric_limits<uint64_t>::max()) {
    if (error)
      *error = "journal sequence exhausted";
    return false;
  }
  // Build the complete candidate first, then validate it. The caller's event
  // is only updated once every field is known to satisfy the reader contract.
  Event candidate = *event;
  candidate.sequence = next_sequence_;
  if (candidate.id.empty())
    candidate.id = EventId(candidate.sequence, candidate.payload);
  if (candidate.timestamp_ms == 0)
    candidate.timestamp_ms =
        std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::system_clock::now().time_since_epoch())
            .count();
  if (!ValidateEventFields(candidate, error))
    return false;
  std::ostringstream canonical;
  canonical << candidate.sequence << '|' << candidate.id << '|'
            << static_cast<int>(candidate.type) << '|' << candidate.timestamp_ms
            << '|' << candidate.schema_version << '|' << candidate.payload;
  candidate.checksum = ChecksumHex(canonical.str());

  // Durability boundary: the caller's Event is updated only after fsync.
  const std::string record =
      std::to_string(candidate.sequence) + '|' + candidate.id + '|' +
      std::to_string(static_cast<int>(candidate.type)) + '|' +
      std::to_string(candidate.timestamp_ms) + '|' +
      std::to_string(candidate.schema_version) + '|' +
      HexEncode(candidate.payload) + '|' + candidate.checksum + '\n';
  const int fd =
      open(path_.c_str(), O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0600);
  if (fd < 0) {
    if (error)
      *error = "cannot open journal for append";
    return false;
  }
  size_t written = 0;
  while (written < record.size()) {
    const ssize_t count =
        write(fd, record.data() + written, record.size() - written);
    if (count < 0 && errno == EINTR)
      continue;
    if (count <= 0) {
      close(fd);
      healthy_ = false;
      if (error)
        *error = "journal write failed";
      return false;
    }
    written += static_cast<size_t>(count);
  }
  if (fchmod(fd, 0600) != 0 || fsync(fd) != 0) {
    close(fd);
    healthy_ = false;
    if (error)
      *error = "journal synchronization failed";
    return false;
  }
  close(fd);
  *event = candidate;
  next_sequence_ = candidate.sequence + 1;
  ++size_;
  if (error)
    error->clear();
  return true;
}

std::vector<Event> Journal::Replay(std::string* error) const {
  std::vector<Event> events;
  std::ifstream input(path_, std::ios::binary);
  if (!input.is_open()) {
    if (std::filesystem::exists(path_)) {
      if (error)
        *error = "cannot open journal for replay";
    } else if (error) {
      error->clear();
    }
    return events;
  }
  const std::string content((std::istreambuf_iterator<char>(input)),
                            std::istreambuf_iterator<char>());
  if (!content.empty() && content.back() != '\n') {
    if (error)
      *error = "incomplete trailing journal record";
    return {};
  }
  std::vector<std::string> lines;
  std::string line;
  std::istringstream stream(content);
  while (std::getline(stream, line)) {
    if (line.empty()) {
      if (error)
        *error = "blank journal record";
      return {};
    }
    lines.push_back(line);
  }
  uint64_t expected = 1;
  for (const std::string& record : lines) {
    line = record;
    if (line.empty())
      continue;
    std::vector<std::string> fields;
    std::stringstream ss(line);
    std::string field;
    while (std::getline(ss, field, '|'))
      fields.push_back(field);
    if (fields.size() != 7) {
      if (error)
        *error = "malformed journal line";
      return {};
    }
    Event event;
    int32_t type = 0;
    int32_t schema = 0;
    if (!ParseUnsigned64(fields[0], &event.sequence) ||
        !ParseInteger32(fields[2], &type) ||
        !ParseInteger64(fields[3], &event.timestamp_ms) ||
        !ParseInteger32(fields[4], &schema)) {
      if (error)
        *error = "non-numeric journal field";
      return {};
    }
    event.type = static_cast<EventType>(type);
    event.schema_version = schema;
    event.id = fields[1];
    event.payload = HexDecode(fields[5]);
    event.checksum = fields[6];
    if (!ValidateEventFields(event, error))
      return {};
    std::ostringstream canonical;
    canonical << event.sequence << '|' << event.id << '|'
              << static_cast<int>(event.type) << '|' << event.timestamp_ms
              << '|' << event.schema_version << '|' << event.payload;
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
  if (error)
    error->clear();
  return events;
}

// ---------------------------------------------------------------------------
// Minimal JSON parser.
// ---------------------------------------------------------------------------
class JsonParser {
 public:
  explicit JsonParser(std::string_view text) : text_(text) {
  }

  bool Parse(Json* out, std::string* error) {
    SkipSpace();
    if (!Value(out)) {
      if (error)
        *error = "JSON parse error at offset " + std::to_string(pos_);
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
    if (pos_ >= text_.size())
      return false;
    const char c = text_[pos_];
    if (c == '{')
      return Object(out);
    if (c == '[')
      return Array(out);
    if (c == '"') {
      std::string value;
      if (!String(&value))
        return false;
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
    if (text_[pos_] != '"')
      return false;
    ++pos_;
    out->clear();
    while (pos_ < text_.size()) {
      const char c = text_[pos_++];
      if (c == '"')
        return true;
      if (c != '\\') {
        out->push_back(c);
        continue;
      }
      if (pos_ >= text_.size())
        return false;
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
          if (pos_ + 4 > text_.size())
            return false;
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
      if (text_[pos_] >= '0' && text_[pos_] <= '9')
        digits = true;
      ++pos_;
    }
    if (!digits)
      return false;
    const std::string token(text_.substr(start, pos_ - start));
    const bool integral = token.find_first_of(".eE") == std::string::npos;
    if (integral) {
      const char* begin = token.data();
      const char* end = begin + token.size();
      if (token.front() == '+')
        ++begin;
      if (token.front() == '-') {
        int64_t signed_value = 0;
        const auto parsed = std::from_chars(begin, end, signed_value, 10);
        if (parsed.ec != std::errc() || parsed.ptr != end)
          return false;
        *out = Json::Integer(signed_value);
        return true;
      }
      uint64_t unsigned_value = 0;
      const auto parsed = std::from_chars(begin, end, unsigned_value, 10);
      if (parsed.ec != std::errc() || parsed.ptr != end)
        return false;
      *out = Json::UInteger(unsigned_value);
      return true;
    }
    double value = 0.0;
    if (!ParseFloating(token, &value))
      return false;
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
      if (!Value(&value))
        return false;
      values.push_back(value);
      SkipSpace();
      if (pos_ >= text_.size())
        return false;
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
      if (!String(&key))
        return false;
      SkipSpace();
      if (pos_ >= text_.size() || text_[pos_] != ':')
        return false;
      ++pos_;
      if (values.find(key) != values.end())
        return false;  // Duplicate object keys are rejected by the contract.
      Json value;
      if (!Value(&value))
        return false;
      values[key] = value;
      SkipSpace();
      if (pos_ >= text_.size())
        return false;
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

Json Json::Null() {
  return Json();
}
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
Json Json::Integer(int64_t value) {
  Json json;
  json.type_ = Type::kInteger;
  json.integer_ = value;
  return json;
}
Json Json::UInteger(uint64_t value) {
  Json json;
  json.type_ = Type::kUInteger;
  json.uinteger_ = value;
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
  switch (type_) {
    case Type::kNumber:
      return number_;
    case Type::kInteger:
      return static_cast<double>(integer_);
    case Type::kUInteger:
      return static_cast<double>(uinteger_);
    default:
      return fallback;
  }
}
int64_t Json::as_int(int64_t fallback) const {
  int64_t value = 0;
  if (as_integer(&value))
    return value;
  if (type_ == Type::kNumber && std::isfinite(number_) &&
      number_ >= static_cast<double>(std::numeric_limits<int64_t>::min()) &&
      number_ <= static_cast<double>(std::numeric_limits<int64_t>::max()) &&
      std::floor(number_) == number_)
    return static_cast<int64_t>(number_);
  if (type_ == Type::kUInteger &&
      uinteger_ <= static_cast<uint64_t>(std::numeric_limits<int64_t>::max()))
    return static_cast<int64_t>(uinteger_);
  return fallback;
}
bool Json::as_integer(int64_t* out) const {
  if (out == nullptr)
    return false;
  if (type_ == Type::kInteger) {
    *out = integer_;
    return true;
  }
  if (type_ == Type::kUInteger &&
      uinteger_ <= static_cast<uint64_t>(std::numeric_limits<int64_t>::max())) {
    *out = static_cast<int64_t>(uinteger_);
    return true;
  }
  return false;
}
bool Json::as_uinteger(uint64_t* out) const {
  if (out == nullptr)
    return false;
  if (type_ == Type::kUInteger) {
    *out = uinteger_;
    return true;
  }
  if (type_ == Type::kInteger && integer_ >= 0) {
    *out = static_cast<uint64_t>(integer_);
    return true;
  }
  return false;
}
std::string Json::as_string(std::string fallback) const {
  return type_ == Type::kString ? string_ : std::move(fallback);
}
const Json* Json::Find(std::string_view key) const {
  if (type_ != Type::kObject)
    return nullptr;
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
    case Type::kInteger:
      return std::to_string(integer_);
    case Type::kUInteger:
      return std::to_string(uinteger_);
    case Type::kString:
      return "\"" + string_ + "\"";
    case Type::kArray: {
      std::string out = "[";
      for (size_t i = 0; i < array_.size(); ++i) {
        if (i > 0)
          out += ",";
        out += array_[i].Dump();
      }
      return out + "]";
    }
    case Type::kObject: {
      std::string out = "{";
      bool first = true;
      for (const auto& [key, value] : object_) {
        if (!first)
          out += ",";
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
    if (error)
      *error = "zlib initialisation failed";
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
    if (status == Z_STREAM_END)
      break;
    if (status != Z_OK && status != Z_BUF_ERROR)
      break;
  }
  inflateEnd(&stream);
  if (status != Z_STREAM_END && error) {
    *error = "gzip decompression failed";
    return {};
  }
  if (error)
    error->clear();
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
  if (!output.is_open())
    return "cannot write " + path;
  output << content;
  return output.good() ? "" : "write failed for " + path;
}

bool ReadCsvText(const std::string& text,
                 std::vector<Candle>* candles,
                 std::string* error,
                 const std::string& csv_schema) {
  candles->clear();
  std::istringstream input(text);
  std::string line;
  const std::string expected_header =
      csv_schema == "v3" ? "date,open,high,low,close,volume,open_interest"
                         : "date,open,high,low,close,volume";
  if (!std::getline(input, line) || line != expected_header) {
    if (error)
      *error = "invalid candle header for " + csv_schema;
    return false;
  }
  std::set<std::string> dates;
  int line_number = 1;
  while (std::getline(input, line)) {
    ++line_number;
    std::vector<std::string> fields;
    std::stringstream ss(line);
    std::string field;
    while (std::getline(ss, field, ','))
      fields.push_back(field);
    const size_t expected_width = csv_schema == "v3" ? 7 : 6;
    if (fields.size() != expected_width) {
      if (error)
        *error =
            "candle row width mismatch at line " + std::to_string(line_number);
      return false;
    }
    if (!IsCanonicalExchangeDate(fields[0])) {
      if (error)
        *error = "invalid calendar date at line " + std::to_string(line_number);
      return false;
    }
    if (!dates.insert(fields[0]).second) {
      if (error)
        *error =
            "duplicate session date at line " + std::to_string(line_number);
      return false;
    }
    Candle candle;
    candle.date = fields[0];
    if (!ParseFloating(fields[1], &candle.open) ||
        !ParseFloating(fields[2], &candle.high) ||
        !ParseFloating(fields[3], &candle.low) ||
        !ParseFloating(fields[4], &candle.close) ||
        !ParseFloating(fields[5], &candle.volume)) {
      if (error)
        *error = "malformed candle row at line " + std::to_string(line_number);
      return false;
    }
    // Volume is a non-negative integral count. Doubles above 2^53 cannot
    // represent every integer, so larger counts are rejected, not rounded.
    if (!std::isfinite(candle.open) || !std::isfinite(candle.high) ||
        !std::isfinite(candle.low) || !std::isfinite(candle.close) ||
        !std::isfinite(candle.volume) || candle.low <= 0.0 ||
        candle.close <= 0.0 ||
        candle.low > std::min(candle.open, candle.close) ||
        std::max(candle.open, candle.close) > candle.high ||
        candle.volume < 0.0 || candle.volume > 9007199254740992.0 ||
        candle.volume != std::floor(candle.volume)) {
      if (error)
        *error =
            "invalid OHLCV relationship at line " + std::to_string(line_number);
      return false;
    }
    if (csv_schema == "v3") {
      int64_t open_interest = 0;
      const auto result = std::from_chars(
          fields[6].data(), fields[6].data() + fields[6].size(), open_interest);
      if (result.ec != std::errc() ||
          result.ptr != fields[6].data() + fields[6].size() ||
          open_interest < -1) {
        if (error)
          *error =
              "invalid open interest at line " + std::to_string(line_number);
        return false;
      }
      candle.open_interest = open_interest;
    }
    candles->push_back(candle);
  }
  std::sort(candles->begin(),
            candles->end(),
            [](const Candle& a, const Candle& b) { return a.date < b.date; });
  return true;
}
bool ReadCsvCandles(const std::string& path,
                    std::vector<Candle>* candles,
                    std::string* error) {
  std::ifstream input(path, std::ios::binary);
  if (!input.is_open()) {
    if (error)
      *error = "cannot open " + path;
    return false;
  }
  const std::string bytes((std::istreambuf_iterator<char>(input)),
                          std::istreambuf_iterator<char>());
  return ReadCsvText(bytes, candles, error);
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
  for (const std::string& line : report.rejections)
    oss << "- " << line << "\n";
  oss << "\n## Labels\n\n";
  oss << "VERIFIED = directly observed; ESTIMATED = modelled; SIMULATED = no "
         "real "
         "orders; INCONCLUSIVE = insufficient evidence; NOT IMPLEMENTED = "
         "absent.\n";
  return oss.str();
}

}  // namespace quant
