// SPDX-License-Identifier: GPL-2.0-or-later
// bbport: the few POSIX calls the portable C++ tests need, on both Linux and MSYS2 CLANG64.
//
// mingw-w64 has no setenv/unsetenv/mkstemp, which is why the existing upscaler test could not
// build in Windows CI. Everything here is header-only and standard C++ where possible.

#pragma once

#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <string>
#include <system_error>

namespace TestEnv {

inline void Set(const char* name, const char* value) {
#ifdef _WIN32
    _putenv_s(name, value); // an empty value removes the variable
#else
    setenv(name, value, 1);
#endif
}

inline void Unset(const char* name) {
#ifdef _WIN32
    _putenv_s(name, "");
#else
    unsetenv(name);
#endif
}

/// An empty file in the system temporary directory, or "" when none could be made.
inline std::string TempFile(const char* prefix) {
    std::error_code error;
    const auto directory = std::filesystem::temp_directory_path(error);
    if (error) return {};
    for (int index = 0; index < 1000; ++index) {
        const auto path =
            directory / (std::string{prefix} + std::to_string(index) + ".ini");
        if (std::filesystem::exists(path, error) && !error) continue;
        std::ofstream file(path, std::ios::trunc);
        if (file) return path.string();
    }
    return {};
}

} // namespace TestEnv
