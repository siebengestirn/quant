#include "quant.hpp"

#include <array>
#include <cctype>
#include <cstdio>
#include <memory>
#include <sstream>

namespace quant {
namespace {

bool SafeServiceName(std::string_view service) {
  if (service.empty())
    return false;
  for (char c : service) {
    if (!(std::isalnum(static_cast<unsigned char>(c)) || c == '_'))
      return false;
  }
  return true;
}

// Reads a single Keychain generic password. The value never enters logs,
// exceptions, configuration, journal events or report files.
std::string ReadKeychainValue(const std::string& service, bool include_value) {
  if (!SafeServiceName(service))
    return "";
  std::string command = "security find-generic-password -s '" + service + "'";
  if (include_value)
    command += " -w";
  command += " 2>/dev/null";
  std::array<char, 4096> buffer{};
  std::string output;
  FILE* pipe = popen(command.c_str(), "r");
  if (pipe == nullptr)
    return "";
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

}  // namespace

Secret ResolveSecret(const Config& config, const std::string& suffix) {
  Secret secret;
  secret.value = ReadKeychainValue(config.keychain_prefix + suffix, true);
  return secret;
}

std::vector<CredentialStatus> InspectCredentials(const Config& config) {
  const std::vector<std::string> services = {"FYERS_APP_ID",
                                             "FYERS_APP_SECRET",
                                             "FYERS_ACCESS_TOKEN",
                                             "UPSTOX_ANALYTICS_TOKEN",
                                             "KITE_API_KEY",
                                             "KITE_API_SECRET",
                                             "KITE_ACCESS_TOKEN"};
  std::vector<CredentialStatus> statuses;
  for (const std::string& suffix : services) {
    CredentialStatus status;
    status.service = config.keychain_prefix + suffix;
    status.present = !ReadKeychainValue(status.service, false).empty();
    if (status.present) {
      status.note = "present (value never read by status command)";
    } else if (suffix == "KITE_ACCESS_TOKEN") {
      status.note =
          "absent: interactive Kite login required for read-only account APIs";
    } else {
      status.note = "absent";
    }
    statuses.push_back(status);
  }
  return statuses;
}

// ---------------------------------------------------------------------------
// Order state machine. Fail-closed around ambiguous submissions.
// ---------------------------------------------------------------------------
bool OrderStateMachine::IsValidTransition(OrderState from, OrderState to) {
  switch (from) {
    case OrderState::kNew:
      return to == OrderState::kValidated || to == OrderState::kRejected;
    case OrderState::kValidated:
      return to == OrderState::kSent || to == OrderState::kRejected;
    case OrderState::kSent:
      return to == OrderState::kAcknowledged || to == OrderState::kRejected ||
             to == OrderState::kAmbiguous || to == OrderState::kCancelled;
    case OrderState::kAcknowledged:
      return to == OrderState::kPartiallyFilled || to == OrderState::kFilled ||
             to == OrderState::kCancelled || to == OrderState::kRejected ||
             to == OrderState::kAmbiguous;
    case OrderState::kPartiallyFilled:
      return to == OrderState::kFilled || to == OrderState::kCancelled ||
             to == OrderState::kAmbiguous;
    // An ambiguous outcome must be resolved out of band, never guessed.
    case OrderState::kAmbiguous:
    case OrderState::kFilled:
    case OrderState::kRejected:
    case OrderState::kCancelled:
      return false;
  }
  return false;
}

bool OrderStateMachine::Transition(OrderState to) {
  if (!IsValidTransition(state_, to))
    return false;
  state_ = to;
  history_.push_back(to);
  return true;
}

// ---------------------------------------------------------------------------
// Zerodha gateway.
// ---------------------------------------------------------------------------
ZerodhaGateway::ZerodhaGateway(Config config, Journal* journal)
    : config_(std::move(config)), journal_(journal) {
}

bool ZerodhaGateway::ReadOnlyConnectivity(std::string* detail,
                                          std::string* error) const {
  const Secret api_key = ResolveSecret(config_, "KITE_API_KEY");
  const Secret session = ResolveSecret(config_, "KITE_ACCESS_TOKEN");
  if (api_key.value.empty()) {
    if (detail)
      *detail = "Kite api_key absent";
    if (error)
      *error = "Kite api_key absent";
    return false;
  }
  if (session.value.empty()) {
    if (detail) {
      *detail =
          "Kite api_key present, session access token absent; "
          "interactive login/request-token handshake required. "
          "No authenticated call attempted.";
    }
    if (error)
      *error = "Kite session access token absent";
    return false;
  }
  const HttpClient http(config_.http_timeout_seconds);
  const HttpResponse response = http.Get(
      "https://api.kite.trade/user/profile",
      {{"Authorization", "token=" + api_key.value + ":" + session.value},
       {"Accept", "application/json"}});
  Json root;
  std::string parse_error;
  if (!Json::Parse(response.body, &root, &parse_error) ||
      response.status != 200) {
    if (detail)
      *detail = "Kite rejected the session (status " +
                std::to_string(response.status) + ")";
    if (error)
      *error = parse_error.empty() ? "Kite API error" : parse_error;
    return false;
  }
  if (detail) {
    *detail =
        "Kite profile endpoint reachable (read-only); "
        "no order endpoints contacted";
  }
  return true;
}

OrderResult ZerodhaGateway::Submit(const OrderRequest& request) const {
  OrderResult result;
  result.accepted = false;
  result.simulated = false;
  result.status = "LOCKED";
  result.message =
      "LIVE EXECUTION LOCKED. No order was transmitted. Requires explicit "
      "authorization, QUANT_LIVE_AUTHORIZED=1, mode=LIVE, and verified broker "
      "and exchange compliance (broker-server hosting, static IP eligibility, "
      "algo tagging, rate limits).";
  (void)request;
  ComplianceGateState gate;
  std::string gate_error;
  if (LiveRouteAuthorized(gate, &gate_error)) {
    result.status = "LOCKED";
    result.message = "LIVE EXECUTION LOCKED: " + gate_error;
  }
  if (journal_ != nullptr) {
    Json::Object payload;
    payload["event"] = Json::String("LIVE_ORDER_BLOCKED");
    payload["symbol"] = Json::String(request.symbol);
    payload["client_order_id"] = Json::String(request.client_order_id);
    payload["mode"] = Json::String(std::string(ModeName(config_.mode)));
    Event event;
    event.type = EventType::kOrder;
    event.payload = Json::ObjectValue(payload).Dump();
    std::string ignored;
    journal_->Append(&event, &ignored);
  }
  return result;
}

std::string ZerodhaGateway::ComplianceGate() const {
  std::ostringstream oss;
  oss << "execution_mode=" << ModeName(config_.mode) << " live_trading_enabled="
      << (config_.live_trading_enabled ? "true" : "false")
      << " margin=" << (config_.allow_margin ? "true" : "false")
      << " short_selling=" << (config_.allow_short_selling ? "true" : "false")
      << " live_submission=LOCKED (unconditional in this build)";
  return oss.str();
}

}  // namespace quant
