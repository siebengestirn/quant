#include "quant.hpp"

#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <vector>
#include <string>

namespace {

int Usage() {
  std::cerr << "usage: quant_contract write <dir> <spec.json>\n"
               "       quant_contract verify <dir>\n";
  return 2;
}

std::string Read(const std::string& path, bool* ok) {
  std::ifstream input(path, std::ios::binary);
  if (!input.is_open()) {
    *ok = false;
    return "";
  }
  *ok = true;
  return std::string((std::istreambuf_iterator<char>(input)),
                     std::istreambuf_iterator<char>());
}

}  // namespace

int main(int argc, char** argv) {
  if (argc < 3)
    return Usage();
  const std::string action = argv[1];
  const std::string directory = argv[2];
  std::string error;
  if (action == "write") {
    if (argc != 4)
      return Usage();
    bool ok = false;
    const std::string spec = Read(argv[3], &ok);
    if (!ok) {
      std::cerr << "cannot read specification\n";
      return 2;
    }
    quant::Json parsed;
    if (!quant::Json::Parse(spec, &parsed, &error)) {
      std::cerr << "invalid specification: " << error << "\n";
      return 2;
    }
    quant::DatasetExpectations expected;
    if (!quant::ParseDatasetExpectations(parsed, &expected, &error)) {
      std::cerr << "invalid specification: " << error << "\n";
      return 2;
    }
    if (!quant::WriteDatasetManifest(directory, expected, &error)) {
      std::cerr << "manifest write failed: " << error << "\n";
      return 1;
    }
    std::cout << "written\n";
    return 0;
  }
  if (action == "verify") {
    if (argc != 3)
      return Usage();
    quant::DatasetExpectations expected;
    if (!quant::ReadDatasetExpectations(directory, &expected, &error)) {
      std::cerr << "contract unreadable: " << error << "\n";
      return 2;
    }
    const quant::DatasetVerification result =
        quant::VerifyDataset(directory, &expected, &error);
    const char* status = "UNKNOWN";
    switch (result.status) {
      case quant::DatasetStatus::kComplete:
        status = "COMPLETE";
        break;
      case quant::DatasetStatus::kPartial:
        status = "PARTIAL";
        break;
      case quant::DatasetStatus::kBlocked:
        status = "BLOCKED";
        break;
      case quant::DatasetStatus::kFailed:
        status = "FAILED";
        break;
    }
    quant::Json::Object output;
    output["status"] = quant::Json::String(status);
    output["contract_digest"] =
        quant::Json::String(quant::DatasetContractDigest(expected));
    std::vector<quant::Json> errors;
    for (const std::string& item : result.errors)
      errors.push_back(quant::Json::String(item));
    output["errors"] = quant::Json::Array(errors);
    quant::Json::Object digests;
    for (const auto& [symbol, digest] : result.file_digests)
      digests[symbol] = quant::Json::String(digest);
    output["file_digests"] = quant::Json::ObjectValue(digests);
    std::cout << quant::Json::ObjectValue(output).Dump() << "\n";
    return result.status == quant::DatasetStatus::kComplete ? 0 : 1;
  }
  return Usage();
}
