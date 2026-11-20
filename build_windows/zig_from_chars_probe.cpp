#include <charconv>
#include <system_error>

int main() {
    const char text[] = "1.25";
    double value = 0.0;
    const auto result = std::from_chars(text, text + sizeof(text) - 1, value);
    return result.ec == std::errc{} ? 0 : 1;
}
