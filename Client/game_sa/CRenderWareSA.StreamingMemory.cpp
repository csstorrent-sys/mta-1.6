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

    // Somnis keeps a little free streaming headroom instead of waiting until the
    // next model/TXD allocation is already touching the limit. This reduces the
    // repeated load -> purge -> load oscillation that presents as micro-stutter.
    constexpr std::uint32_t kMinReserveBytes = 8U * 1024U * 1024U;
    constexpr std::uint32_t kMaxReserveBytes = 24U * 1024U * 1024U;
    constexpr std::uint32_t kHighWaterPercent = 90U;
    constexpr std::uint32_t kEmergencyWaterPercent = 97U;
    constexpr std::uint32_t kTargetWaterPercent = 82U;
    constexpr std::uint32_t kNormalPurgeCooldownMs = 120U;
    constexpr std::uint32_t kNormalPurgeCapBytes = 32U * 1024U * 1024U;

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

        std::uint32_t memoryUsedBytes = 0;
        __try
        {
            memoryUsedBytes = *reinterpret_cast<volatile std::uint32_t*>(VAR_CStreaming_memoryUsed);
        }
        __except (EXCEPTION_EXECUTE_HANDLER)
        {
            return;
        }

        if (memoryUsedBytes > memoryLimitBytes)
            memoryUsedBytes = memoryLimitBytes;

        const std::uint32_t freeBytes = memoryLimitBytes - memoryUsedBytes;

        // Reserve scales with the configured pool but stays deliberately small.
        // 320 MB therefore keeps roughly 20 MB ready for the next burst.
        const std::uint32_t reserveBytes = std::min(
            kMaxReserveBytes,
            std::max(kMinReserveBytes, static_cast<std::uint32_t>(static_cast<std::uint64_t>(memoryLimitBytes) / 16ULL)));

        const std::uint32_t boundedEstimate = std::min(estimatedBytes, kMaxReasonableEstimate);
        const std::uint64_t desiredFree64 = static_cast<std::uint64_t>(boundedEstimate) + reserveBytes;
        const std::uint32_t desiredFree = static_cast<std::uint32_t>(
            std::min<std::uint64_t>(desiredFree64, static_cast<std::uint64_t>(memoryLimitBytes - 1)));

        std::uint32_t bytesToClean = 0;

        // Normal path: free enough room for the incoming asset plus a safety reserve.
        if (freeBytes < desiredFree)
            bytesToClean = desiredFree - freeBytes;

        // Pressure path: if the pool is already above the high-water mark, ask GTA's
        // streamer to cool it down towards a lower target. The hysteresis between 90%
        // and 82% avoids cleaning on every nearby model request.
        const std::uint64_t highWaterBytes = (static_cast<std::uint64_t>(memoryLimitBytes) * kHighWaterPercent) / 100ULL;
        if (memoryUsedBytes >= highWaterBytes)
        {
            const std::uint64_t targetUsedBytes = (static_cast<std::uint64_t>(memoryLimitBytes) * kTargetWaterPercent) / 100ULL;
            if (memoryUsedBytes > targetUsedBytes)
            {
                const std::uint32_t pressureClean = static_cast<std::uint32_t>(
                    std::min<std::uint64_t>(memoryUsedBytes - targetUsedBytes, std::numeric_limits<std::uint32_t>::max()));
                bytesToClean = std::max(bytesToClean, pressureClean);
            }
        }

        if (bytesToClean < kMinBytesToClean)
            return;

        // Do not hammer MakeSpaceFor dozens of times during one short asset burst.
        // Normal pressure is smoothed into small, spaced purges. Only an emergency
        // condition bypasses this cooldown because then preventing OOM is more important
        // than avoiding a small hitch.
        const std::uint64_t emergencyWaterBytes = (static_cast<std::uint64_t>(memoryLimitBytes) * kEmergencyWaterPercent) / 100ULL;
        const bool          bEmergency = memoryUsedBytes >= emergencyWaterBytes || freeBytes < boundedEstimate;

        static std::uint32_t s_uiLastPurgeTick = 0;
        const std::uint32_t  uiNow = static_cast<std::uint32_t>(GetTickCount());
        const std::uint32_t  uiSinceLastPurge = uiNow - s_uiLastPurgeTick;
        if (!bEmergency && s_uiLastPurgeTick != 0 && uiSinceLastPurge < kNormalPurgeCooldownMs)
            return;

        // Normal cleanup is deliberately chunked. A huge one-shot purge can throw out
        // many useful TXD/DFF assets and immediately force the async streamer to load them
        // again. Emergency cleanup retains the larger historical safety cap.
        if (!bEmergency)
            bytesToClean = std::min(bytesToClean, kNormalPurgeCapBytes);

        const std::uint32_t maxClean = static_cast<std::uint32_t>((static_cast<std::uint64_t>(memoryLimitBytes) * 3ULL) / 4ULL);
        bytesToClean = std::min(bytesToClean, std::max(kMinBytesToClean, maxClean));

        __try
        {
            pStreaming->MakeSpaceFor(bytesToClean);
            s_uiLastPurgeTick = uiNow;
        }
        __except (EXCEPTION_EXECUTE_HANDLER)
        {
            return;
        }
    }

}
