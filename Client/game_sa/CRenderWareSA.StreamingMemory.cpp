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
    constexpr std::uint32_t kLowFpsPurgeCapBytes = 8U * 1024U * 1024U;
    constexpr std::uint32_t kMidFpsPurgeCapBytes = 16U * 1024U * 1024U;

    // Win32 MTA can still run out of virtual address space even while Windows has
    // plenty of physical RAM. Track fragmentation as well as GTA's own pool usage.
    constexpr std::uint64_t kAddressSpacePressureTotalFree = 384ULL * 1024ULL * 1024ULL;
    constexpr std::uint64_t kAddressSpaceCriticalTotalFree = 192ULL * 1024ULL * 1024ULL;
    constexpr std::uint64_t kAddressSpacePressureLargestBlock = 96ULL * 1024ULL * 1024ULL;
    constexpr std::uint64_t kAddressSpaceCriticalLargestBlock = 48ULL * 1024ULL * 1024ULL;
    constexpr std::uint32_t kAddressSpacePressureCleanupBytes = 16U * 1024U * 1024U;
    constexpr std::uint32_t kAddressSpaceCriticalCleanupBytes = 32U * 1024U * 1024U;
    constexpr std::uint32_t kAddressSpaceScanCooldownMs = 1000U;

    // Keep this deliberately conservative. MTA/GTA:SA is still a Win32 process, so
    // blindly assigning 512 MB+ to the GTA streaming pool can steal address-space
    // headroom from CEF, Lua, custom textures/models and the D3D driver.
    constexpr std::size_t   kSomnisStreamingTargetBytes = 320ULL * 1024ULL * 1024ULL;
    constexpr std::uint64_t kSomnisMinSystemRamBytes = 8ULL * 1024ULL * 1024ULL * 1024ULL;
    constexpr std::uint32_t kSomnisMinCalculatedStreamingMB = 256U;

    struct SAddressSpacePressure
    {
        bool          bPressure = false;
        bool          bCritical = false;
        std::uint64_t ullTotalFree = 0;
        std::uint64_t ullLargestFreeBlock = 0;
    };

    const SAddressSpacePressure& GetAddressSpacePressure()
    {
        static SAddressSpacePressure s_State;
        static std::uint32_t         s_uiLastScanTick = 0;

        const std::uint32_t uiNow = static_cast<std::uint32_t>(GetTickCount());
        if (s_uiLastScanTick != 0 && uiNow - s_uiLastScanTick < kAddressSpaceScanCooldownMs)
            return s_State;

        s_uiLastScanTick = uiNow;
        s_State = SAddressSpacePressure{};

        SYSTEM_INFO systemInfo{};
        GetSystemInfo(&systemInfo);

        std::uintptr_t address = reinterpret_cast<std::uintptr_t>(systemInfo.lpMinimumApplicationAddress);
        const std::uintptr_t maximumAddress = reinterpret_cast<std::uintptr_t>(systemInfo.lpMaximumApplicationAddress);

        while (address < maximumAddress)
        {
            MEMORY_BASIC_INFORMATION mbi{};
            if (VirtualQuery(reinterpret_cast<LPCVOID>(address), &mbi, sizeof(mbi)) != sizeof(mbi))
                break;

            if (mbi.State == MEM_FREE)
            {
                const std::uint64_t regionSize = static_cast<std::uint64_t>(mbi.RegionSize);
                s_State.ullTotalFree += regionSize;
                s_State.ullLargestFreeBlock = std::max(s_State.ullLargestFreeBlock, regionSize);
            }

            const std::uintptr_t baseAddress = reinterpret_cast<std::uintptr_t>(mbi.BaseAddress);
            const std::uint64_t nextAddress64 = static_cast<std::uint64_t>(baseAddress) + static_cast<std::uint64_t>(mbi.RegionSize);
            if (nextAddress64 <= address || nextAddress64 > static_cast<std::uint64_t>(maximumAddress) + 1ULL)
                break;

            address = static_cast<std::uintptr_t>(nextAddress64);
        }

        s_State.bCritical = s_State.ullTotalFree < kAddressSpaceCriticalTotalFree ||
                            s_State.ullLargestFreeBlock < kAddressSpaceCriticalLargestBlock;
        s_State.bPressure = s_State.bCritical || s_State.ullTotalFree < kAddressSpacePressureTotalFree ||
                            s_State.ullLargestFreeBlock < kAddressSpacePressureLargestBlock;
        return s_State;
    }

    std::uint32_t GetNormalPurgeCap()
    {
        if (!pGame)
            return kNormalPurgeCapBytes;

        const float fFPS = pGame->GetFPS();
        if (fFPS > 0.0f && fFPS < 25.0f)
            return kLowFpsPurgeCapBytes;
        if (fFPS > 0.0f && fFPS < 40.0f)
            return kMidFpsPurgeCapBytes;
        return kNormalPurgeCapBytes;
    }

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

        // GTA's pool counters do not tell us when the whole Win32 address space is badly
        // fragmented. When that happens, proactively unload some streamable assets before
        // the next DFF/TXD/CEF/D3D allocation becomes the crash that finally exposes it.
        const SAddressSpacePressure& addressPressure = GetAddressSpacePressure();
        if (addressPressure.bCritical)
            bytesToClean = std::max(bytesToClean, kAddressSpaceCriticalCleanupBytes);
        else if (addressPressure.bPressure)
            bytesToClean = std::max(bytesToClean, kAddressSpacePressureCleanupBytes);

        if (bytesToClean < kMinBytesToClean)
            return;

        const std::uint64_t emergencyWaterBytes = (static_cast<std::uint64_t>(memoryLimitBytes) * kEmergencyWaterPercent) / 100ULL;
        const bool bEmergency = memoryUsedBytes >= emergencyWaterBytes || freeBytes < boundedEstimate || addressPressure.bCritical;

        static std::uint32_t s_uiLastPurgeTick = 0;
        const std::uint32_t  uiNow = static_cast<std::uint32_t>(GetTickCount());
        const std::uint32_t  uiSinceLastPurge = uiNow - s_uiLastPurgeTick;
        if (!bEmergency && s_uiLastPurgeTick != 0 && uiSinceLastPurge < kNormalPurgeCooldownMs)
            return;

        // Normal cleanup is deliberately chunked and becomes even smaller while FPS is
        // already low. Emergency cleanup keeps the larger historical safety cap because
        // avoiding OOM/address-space exhaustion is more important than one hitch.
        if (!bEmergency)
            bytesToClean = std::min(bytesToClean, GetNormalPurgeCap());

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
