/*****************************************************************************
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
