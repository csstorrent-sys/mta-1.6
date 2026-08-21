/*****************************************************************************
 *
 *  PROJECT:     Multi Theft Auto v1.0
 *  LICENSE:     See LICENSE in the top level directory
 *  FILE:        game_sa/CRenderWareSA.StreamingMemory.cpp
 *  PURPOSE:     Streaming memory purging (MakeSpaceFor) to reduce OOM
 *
 *****************************************************************************/

#include "StdInc.h"
#include "CGameSA.h"
#include "CRenderWareSA.StreamingMemory.h"
#include <core/CCoreInterface.h>

#include <algorithm>
#include <cstdint>
#include <limits>

extern CGameSA*        pGame;
extern CCoreInterface* g_pCore;

#define VAR_CStreaming_memoryAvailableKB 0x08A5A80
#define VAR_CStreaming_memoryUsed        0x08E4CB4

namespace StreamingMemory
{

    constexpr std::uint32_t kMinBytesToClean = 64U * 1024U;
    constexpr std::uint32_t kMaxReasonableEstimate = 512U * 1024U * 1024U;  // 512 MB

    // Keep this deliberately conservative. MTA/GTA:SA is still a Win32 process, so
    // blindly assigning 512 MB+ to the GTA streaming pool can steal address-space
    // headroom from CEF, Lua, custom textures/models and the D3D driver.
    constexpr std::size_t   kSomnisStreamingTargetBytes = 320ULL * 1024ULL * 1024ULL;
    constexpr std::uint64_t kSomnisMinSystemRamBytes = 8ULL * 1024ULL * 1024ULL * 1024ULL;
    constexpr std::uint32_t kSomnisMinCalculatedStreamingMB = 256U;

    void EnsureSomnisStreamingMemoryLimit()
    {
        if (!g_pCore || g_pCore->IsUsingCustomStreamingMemorySize())
            return;

        // Cache the physical-RAM decision; querying WMI repeatedly during texture loads
        // would defeat the purpose of keeping this path lightweight.
        static const bool s_bEnoughSystemRam = GetWMITotalPhysicalMemory() >= kSomnisMinSystemRamBytes;
        if (!s_bEnoughSystemRam)
            return;

        // Reuse MTA's own RAM/VRAM safety calculation as a minimum capability gate.
        // Low-end systems therefore retain their normal 1.6 streaming limit.
        if (g_pCore->GetMaxStreamingMemory() < kSomnisMinCalculatedStreamingMB)
            return;

        if (g_pCore->GetStreamingMemory() < kSomnisStreamingTargetBytes)
            g_pCore->SetCustomStreamingMemory(kSomnisStreamingTargetBytes);
    }

    void PrepareStreamingMemoryForSize(std::uint32_t estimatedBytes)
    {
        EnsureSomnisStreamingMemoryLimit();

        if (estimatedBytes < kMinBytesToClean)
            return;

        if (!pGame)
            return;

        auto* pStreaming = pGame->GetStreaming();
        if (!pStreaming)
            return;

        std::uint32_t memoryLimitKB = 0;
        __try
        {
            memoryLimitKB = *reinterpret_cast<volatile std::uint32_t*>(VAR_CStreaming_memoryAvailableKB);
        }
        __except (EXCEPTION_EXECUTE_HANDLER)
        {
            return;
        }
        if (memoryLimitKB == 0)
            return;

        std::uint64_t memoryLimitBytes64 = static_cast<std::uint64_t>(memoryLimitKB) * 1024ULL;
        if (memoryLimitBytes64 > std::numeric_limits<std::uint32_t>::max())
            memoryLimitBytes64 = std::numeric_limits<std::uint32_t>::max();
        const std::uint32_t memoryLimitBytes = static_cast<std::uint32_t>(memoryLimitBytes64);

        if (memoryLimitBytes <= 1)
            return;

        std::uint32_t bytesToClean = std::min(estimatedBytes, kMaxReasonableEstimate);
        if (bytesToClean >= memoryLimitBytes)
        {
            const std::uint32_t maxClean = static_cast<std::uint32_t>((static_cast<std::uint64_t>(memoryLimitBytes) * 3) / 4);
            bytesToClean = std::min(maxClean, memoryLimitBytes - 1);
        }

        if (bytesToClean == 0)
            return;

        std::uint32_t memoryUsedBytes = 0;
        __try
        {
            memoryUsedBytes = *reinterpret_cast<volatile std::uint32_t*>(VAR_CStreaming_memoryUsed);
        }
        __except (EXCEPTION_EXECUTE_HANDLER)
        {
            return;
        }
        if (memoryUsedBytes < memoryLimitBytes)
        {
            const std::uint32_t freeBytes = memoryLimitBytes - memoryUsedBytes;
            if (freeBytes >= bytesToClean)
                return;
        }

        __try
        {
            pStreaming->MakeSpaceFor(bytesToClean);
        }
        __except (EXCEPTION_EXECUTE_HANDLER)
        {
            return;
        }
    }

}
