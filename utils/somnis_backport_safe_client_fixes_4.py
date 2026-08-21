#!/usr/bin/env python3
"""Fourth Somnis MTA 1.6 client stability/performance backport pass.

This pass removes the recursive startup scan of the entire MTA/resource cache
for PDB files. It is client-only and does not touch network compatibility.
"""
from pathlib import Path

changed = []
skipped = []
already = []


def replace_exact(path: str, old: str, new: str, label: str) -> None:
    p = Path(path)
    if not p.exists():
        skipped.append((label, path, "file missing"))
        return
    text = p.read_text(encoding="utf-8")
    if new in text:
        already.append((label, path))
        return
    if old not in text:
        skipped.append((label, path, "source pattern not found"))
        return
    p.write_text(text.replace(old, new, 1), encoding="utf-8", newline="")
    changed.append((label, path))


def replace_between(path: str, start: str, end: str, replacement: str, label: str) -> None:
    p = Path(path)
    if not p.exists():
        skipped.append((label, path, "file missing"))
        return
    text = p.read_text(encoding="utf-8")
    if replacement in text:
        already.append((label, path))
        return
    start_pos = text.find(start)
    if start_pos < 0:
        skipped.append((label, path, "start marker not found"))
        return
    end_pos = text.find(end, start_pos)
    if end_pos < 0:
        skipped.append((label, path, "end marker not found"))
        return
    end_pos += len(end)
    p.write_text(text[:start_pos] + replacement + text[end_pos:], encoding="utf-8", newline="")
    changed.append((label, path))


def ensure_file(path: str, content: str, label: str) -> None:
    p = Path(path)
    if p.exists():
        if p.read_text(encoding="utf-8") == content:
            already.append((label, path))
        else:
            skipped.append((label, path, "file already exists with different content"))
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8", newline="")
    changed.append((label, path))


helper = r'''/*****************************************************************************
 *
 *  PROJECT:     Multi Theft Auto v1.0
 *  LICENSE:     See LICENSE in the top level directory
 *  FILE:        core/PdbDirectoryDiscovery.h
 *  PURPOSE:     Locate directories containing local crash-handler symbols
 *
 *  Multi Theft Auto is available from https://www.multitheftauto.com/
 *
 *****************************************************************************/

#pragma once

#include <array>
#include <filesystem>
#include <vector>

namespace CrashHandler::Details
{
    [[nodiscard]] inline bool HasPdbExtension(const std::filesystem::path& path)
    {
        const auto extension = path.extension().wstring();
        return extension.size() == 4 && extension[0] == L'.' && (extension[1] == L'p' || extension[1] == L'P') &&
               (extension[2] == L'd' || extension[2] == L'D') && (extension[3] == L'b' || extension[3] == L'B');
    }

    [[nodiscard]] inline bool DirectoryContainsPdb(const std::filesystem::path& directory)
    {
        std::error_code ec{};
        constexpr auto options = std::filesystem::directory_options::skip_permission_denied;
        std::filesystem::directory_iterator iter{directory, options, ec};
        if (ec)
            return false;

        for (const auto end = std::filesystem::directory_iterator{}; iter != end;)
        {
            std::error_code entryEc{};
            const bool isSymlink = iter->is_symlink(entryEc);
            if (!entryEc && !isSymlink && iter->is_regular_file(entryEc) && !entryEc && HasPdbExtension(iter->path()))
                return true;

            iter.increment(ec);
            if (ec)
                ec.clear();
        }
        return false;
    }

    [[nodiscard]] inline std::vector<std::filesystem::path> FindPdbDirectories(const std::filesystem::path& processDirectory)
    {
        std::vector<std::filesystem::path> pdbDirectories;
        if (processDirectory.empty())
            return pdbDirectories;

        // Client PDBs belong beside the launcher/core binaries, not recursively in
        // cached server resources. Keeping this bounded avoids startup work that grows
        // with every downloaded resource/file.
        const std::array candidateDirectories{
            processDirectory,
            processDirectory / L"MTA",
            processDirectory / L"mods" / L"deathmatch",
        };

        for (const auto& directory : candidateDirectories)
        {
            if (DirectoryContainsPdb(directory))
                pdbDirectories.push_back(directory);
        }
        return pdbDirectories;
    }
}
'''

ensure_file("Client/core/PdbDirectoryDiscovery.h", helper, "bounded PDB directory helper")

replace_exact(
    "Client/core/CrashHandler.cpp",
    '''#include "CrashHandler.h"\n#include "StackTraceHelpers.h"\n''',
    '''#include "CrashHandler.h"\n#include "PdbDirectoryDiscovery.h"\n#include "StackTraceHelpers.h"\n''',
    "include bounded PDB discovery helper",
)

replace_between(
    "Client/core/CrashHandler.cpp",
    '''                               const std::filesystem::path rootPath{FromUTF8(processDir)};''',
    '''                               pdbDirs.assign(uniqueDirs.begin(), uniqueDirs.end());''',
    '''                               const auto directories = CrashHandler::Details::FindPdbDirectories({FromUTF8(processDir)});\n                               pdbDirs.reserve(directories.size());\n\n                               for (const auto& directory : directories)\n                               {\n                                   const auto utf8Directory = ToUTF8(directory.wstring());\n                                   pdbDirs.emplace_back(utf8Directory.c_str());\n                               }''',
    "replace recursive PDB/resource-cache scan",
)

print("Somnis safe client backports pass 4:")
for label, path in changed:
    print(f"  APPLIED: {label} [{path}]")
for label, path in already:
    print(f"  ALREADY: {label} [{path}]")
for label, path, reason in skipped:
    print(f"  SKIPPED: {label} [{path}] - {reason}")
print(f"summary: applied={len(changed)} already={len(already)} skipped={len(skipped)}")
