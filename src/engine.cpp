#include "quant.hpp"

#include <curl/curl.h>
#include <zlib.h>

#include <cerrno>
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
  // RecognizeExecution when an execution is actually committed.
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
      << '|' << (snapshot.halted ? 1 : 0) << '|' << snapshot.halt_reason;
  return oss.str();
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
          {{"version", Json::Number(committed.version)},
           {"account_id", Json::String(committed.account_id)},
           {"session_id", Json::String(committed.session_id)},
           {"policy_version", Json::String(committed.policy_version)},
           {"journal_sequence", Json::Number(committed.journal_sequence)},
           {"starting_equity_paise",
            Json::Number(committed.starting_equity_paise)},
           {"current_equity_paise",
            Json::Number(committed.current_equity_paise)},
           {"peak_equity_paise", Json::Number(committed.peak_equity_paise)},
           {"low_equity_paise", Json::Number(committed.low_equity_paise)},
           {"realized_pnl_paise", Json::Number(committed.realized_pnl_paise)},
           {"unrealized_pnl_paise",
            Json::Number(committed.unrealized_pnl_paise)},
           {"halted", Json::Bool(committed.halted)},
           {"halt_reason", Json::String(committed.halt_reason)},
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
    if (value == nullptr || value->type() != Json::Type::kNumber)
      return false;
    *out = value->as_int(0);
    return true;
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
      Json::Number(state->starting_equity.paise());
  payload["current_equity_paise"] = Json::Number(state->current_equity.paise());
  payload["peak_equity_paise"] = Json::Number(state->peak_equity.paise());
  payload["low_equity_paise"] = Json::Number(state->low_equity.paise());
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
  Journal journal;
  if (!journal.Open(journal_path, error))
    return false;
  const bool recovered =
      RecoverRiskStateFromJournal(snapshot_path, &journal, state, error);
  journal.Close();
  return recovered;
}

bool RecoverRiskStateFromJournal(const std::string& snapshot_path,
                                 Journal* journal,
                                 DailyRiskState* state,
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
  DailyRiskState recovered;
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
                    std::string* error) {
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
  return true;
}

bool Portfolio::Sell(const std::string& symbol,
                     int quantity,
                     Money price,
                     const CostBreakdown& costs,
                     std::string* error) {
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
                                  int64_t now_ms) {
  RiskDecision decision;
  decision.notional = Money::FromRupees(0.0);
  auto reject = [&decision](const std::string& reason) {
    decision.approved = false;
    decision.quantity = 0;
    decision.reason = reason;
    return decision;
  };

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
  sizing.side = OrderSide::kBuy;
  sizing.price = Money::FromRupees(quote.ask);  // Executable ask for a buy.
  sizing.volatility = signal.features.vol20;
  sizing.stop_distance = config_.stop_loss_fraction;
  sizing.delivery = config_.execution_delivery;
  sizing.available_depth = quote.ask_qty;  // Side-specific depth.
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
  if (!std::filesystem::exists(path_)) {
    const int dir_fd =
        open(directory.c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (dir_fd >= 0) {
      if (fsync(dir_fd) != 0) {
        close(dir_fd);
        flock(lock_fd_, LOCK_UN);
        close(lock_fd_);
        lock_fd_ = -1;
        locked_ = false;
        if (error)
          *error = "journal directory synchronization failed";
        return false;
      }
      close(dir_fd);
    }
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
  *event = candidate;

  // Durability boundary: the record is acknowledged only after fsync succeeds.
  const std::string record =
      std::to_string(event->sequence) + '|' + event->id + '|' +
      std::to_string(static_cast<int>(event->type)) + '|' +
      std::to_string(event->timestamp_ms) + '|' +
      std::to_string(event->schema_version) + '|' + HexEncode(event->payload) +
      '|' + event->checksum + '\n';
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

bool ReadCsvCandles(const std::string& path,
                    std::vector<Candle>* candles,
                    std::string* error) {
  candles->clear();
  std::ifstream input(path);
  if (!input.is_open()) {
    if (error)
      *error = "cannot open " + path;
    return false;
  }
  std::string line;
  std::getline(input, line);  // header.
  while (std::getline(input, line)) {
    std::vector<std::string> fields;
    std::stringstream ss(line);
    std::string field;
    while (std::getline(ss, field, ','))
      fields.push_back(field);
    if (fields.size() < 6)
      continue;
    Candle candle;
    candle.date = fields[0];
    if (!ParseFloating(fields[1], &candle.open) ||
        !ParseFloating(fields[2], &candle.high) ||
        !ParseFloating(fields[3], &candle.low) ||
        !ParseFloating(fields[4], &candle.close) ||
        !ParseFloating(fields[5], &candle.volume)) {
      if (error)
        *error = "malformed candle row in " + path;
      return false;
    }
    candles->push_back(candle);
  }
  std::sort(candles->begin(),
            candles->end(),
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
