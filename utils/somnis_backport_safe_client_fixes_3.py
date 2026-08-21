#!/usr/bin/env python3
"""Third Somnis MTA 1.6 protocol-neutral client stability backport pass.

Backports selected fixes that do not alter network protocol IDs, packet layouts,
bitstream formats, RPC IDs, server compatibility identifiers or server gameplay.
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


# ---------------------------------------------------------------------------
# Drive-by crash: do not abort the native gang-driveby task re-entrantly from
# onClientPlayerWeaponFire/onClientPedWeaponFire. Defer it to the next pulse.
# ---------------------------------------------------------------------------
replace_exact(
    "Client/mods/deathmatch/logic/CClientPed.cpp",
    '''    m_bDestroyingSatchels = false;\n    m_bDoingGangDriveby = false;\n\n    m_pAnimationBlock = NULL;\n''',
    '''    m_bDestroyingSatchels = false;\n    m_bDoingGangDriveby = false;\n    m_bProcessingWeaponFireEvent = false;\n    m_bDeferredGangDrivebyAbort = false;\n\n    m_pAnimationBlock = NULL;\n''',
    "initialize deferred drive-by state",
)

replace_exact(
    "Client/mods/deathmatch/logic/CClientPed.cpp",
    '''        if (m_bPendingRebuildPlayer)\n            ProcessRebuildPlayer(true);\n\n        CControllerState Current;\n''',
    '''        if (m_bPendingRebuildPlayer)\n            ProcessRebuildPlayer(true);\n\n        // A weapon-fire event may request drive-by shutdown while GTA is still inside\n        // CTaskSimpleGangDriveBy::ProcessPed. Abort on the next pulse instead.\n        if (m_bDeferredGangDrivebyAbort)\n        {\n            m_bDeferredGangDrivebyAbort = false;\n            CTask* primaryTask = m_pTaskManager->GetTask(TASK_PRIORITY_PRIMARY);\n            if (primaryTask && primaryTask->GetTaskType() == TASK_SIMPLE_GANG_DRIVEBY)\n                primaryTask->MakeAbortable(m_pPlayerPed, ABORT_PRIORITY_URGENT, NULL);\n        }\n\n        CControllerState Current;\n''',
    "defer drive-by abort until streamed-in pulse",
)

replace_exact(
    "Client/mods/deathmatch/logic/CClientPed.cpp",
    '''    if (primaryTask && primaryTask->GetTaskType() == TASK_SIMPLE_GANG_DRIVEBY)\n    {\n        if (!bDriveby)\n        {\n            primaryTask->MakeAbortable(m_pPlayerPed, ABORT_PRIORITY_URGENT, NULL);\n        }\n    }\n''',
    '''    if (primaryTask && primaryTask->GetTaskType() == TASK_SIMPLE_GANG_DRIVEBY)\n    {\n        if (!bDriveby)\n        {\n            if (m_bProcessingWeaponFireEvent)\n            {\n                m_bDeferredGangDrivebyAbort = true;\n            }\n            else\n            {\n                primaryTask->MakeAbortable(m_pPlayerPed, ABORT_PRIORITY_URGENT, NULL);\n            }\n        }\n    }\n''',
    "avoid re-entrant drive-by task abort",
)

replace_exact(
    "Client/mods/deathmatch/logic/CClientPed.h",
    '''    bool IsDoingGangDriveby();\n    void SetDoingGangDriveby(bool bDriveby);\n\n    bool GetRunningAnimationName(SString& strBlockName, SString& strAnimName);\n''',
    '''    bool IsDoingGangDriveby();\n    void SetDoingGangDriveby(bool bDriveby);\n    void SetProcessingWeaponFireEvent(bool bProcessing) noexcept { m_bProcessingWeaponFireEvent = bProcessing; }\n\n    bool GetRunningAnimationName(SString& strBlockName, SString& strAnimName);\n''',
    "expose weapon-fire dispatch state to ped",
)

replace_exact(
    "Client/mods/deathmatch/logic/CClientPed.h",
    '''    bool                                     m_bDestroyingSatchels;\n    bool                                     m_bDoingGangDriveby;\n    std::unique_ptr<CAnimBlock>              m_pAnimationBlock;\n''',
    '''    bool                                     m_bDestroyingSatchels;\n    bool                                     m_bDoingGangDriveby;\n    bool                                     m_bProcessingWeaponFireEvent;\n    bool                                     m_bDeferredGangDrivebyAbort;\n    std::unique_ptr<CAnimBlock>              m_pAnimationBlock;\n''',
    "store deferred drive-by state",
)

replace_exact(
    "Client/mods/deathmatch/logic/CClientGame.cpp",
    '''                else\n                    Arguments.PushNil();\n\n                if (IS_PLAYER(pPed))\n''',
    '''                else\n                    Arguments.PushNil();\n\n                pPed->SetProcessingWeaponFireEvent(true);\n                if (IS_PLAYER(pPed))\n''',
    "mark weapon-fire event dispatch",
)

replace_exact(
    "Client/mods/deathmatch/logic/CClientGame.cpp",
    '''                else\n                    pPed->CallEvent("onClientPedWeaponFire", Arguments, true);\n            }\n            pPed->PostWeaponFire();\n''',
    '''                else\n                    pPed->CallEvent("onClientPedWeaponFire", Arguments, true);\n\n                pPed->SetProcessingWeaponFireEvent(false);\n            }\n            pPed->PostWeaponFire();\n''',
    "clear weapon-fire event dispatch state",
)


# ---------------------------------------------------------------------------
# Detached vehicle parts retain their source vehicle model ID after explosions.
# Clear those references before a custom vehicle model is deallocated.
# ---------------------------------------------------------------------------
replace_exact(
    "Client/game_sa/CModelInfoSA.cpp",
    '''#include "CPedModelInfoSA.h"\n#include "CPedSA.h"\n#include "CWorldSA.h"\n''',
    '''#include "CPedModelInfoSA.h"\n#include "CPedSA.h"\n#include "CPoolsSA.h"\n#include "CWorldSA.h"\n''',
    "include pools for detached vehicle part cleanup",
)

replace_exact(
    "Client/game_sa/CModelInfoSA.cpp",
    '''        case eModelInfoType::VEHICLE:\n            delete reinterpret_cast<CVehicleModelInfoSAInterface*>(ppModelInfo[m_dwModelID]);\n            break;\n''',
    '''        case eModelInfoType::VEHICLE:\n            static_cast<CPoolsSA*>(pGame->GetPools())->ResetDetachedCarPartsRefModel(static_cast<std::uint16_t>(m_dwModelID));\n            delete reinterpret_cast<CVehicleModelInfoSAInterface*>(ppModelInfo[m_dwModelID]);\n            break;\n''',
    "clear detached car-part model references",
)

replace_exact(
    "Client/game_sa/CObjectSA.h",
    '''    uint32 bExploded : 1;\n    uint32 b0x80 : 1;\n''',
    '''    uint32 bExploded : 1;\n    uint32 bChangesVehColor : 1;\n''',
    "name detached-part repaint flag",
)

replace_exact(
    "Client/game_sa/CObjectSA.h",
    '''    uint8               pad8;                 // 329\n    uint16              pad9;                 // 330\n    uint8               pad10;                // 332\n''',
    '''    uint8               pad8;                 // 329\n    short               sRefModelIndex;       // 330, source vehicle model for detached car parts (-1 = none)\n    uint8               pad10;                // 332\n''',
    "name detached-part source model field",
)

replace_exact(
    "Client/game_sa/CPoolsSA.h",
    '''    DWORD GetPedPoolIndex(std::uint8_t* pInterface);\n    DWORD GetVehiclePoolIndex(std::uint8_t* pInterfacee);\n    DWORD GetObjectPoolIndex(std::uint8_t* pInterface);\n\n    int  GetNumberOfUsedSpaces(ePools pools);\n''',
    '''    DWORD GetPedPoolIndex(std::uint8_t* pInterface);\n    DWORD GetVehiclePoolIndex(std::uint8_t* pInterfacee);\n    DWORD GetObjectPoolIndex(std::uint8_t* pInterface);\n\n    void ResetDetachedCarPartsRefModel(std::uint16_t usModelID) noexcept;\n\n    int  GetNumberOfUsedSpaces(ePools pools);\n''',
    "declare detached car-part cleanup",
)

replace_exact(
    "Client/game_sa/CPoolsSA.cpp",
    '''void CPoolsSA::DeleteAllObjects()\n{\n    while (m_objectPool.ulCount > 0)\n    {\n        CObjectSA* pObject = m_objectPool.arrayOfClientEntities[m_objectPool.ulCount - 1].pEntity;\n\n        RemoveObject(pObject);\n    }\n}\n\n//////////////////////////////////////////////////////////////////////////////////////////\n//                                       PEDS POOL                                      //\n''',
    '''void CPoolsSA::DeleteAllObjects()\n{\n    while (m_objectPool.ulCount > 0)\n    {\n        CObjectSA* pObject = m_objectPool.arrayOfClientEntities[m_objectPool.ulCount - 1].pEntity;\n\n        RemoveObject(pObject);\n    }\n}\n\nvoid CPoolsSA::ResetDetachedCarPartsRefModel(std::uint16_t usModelID) noexcept\n{\n    CPoolSAInterface<CObjectSAInterface>* pObjectPool = *m_ppObjectPoolInterface;\n    if (!pObjectPool)\n        return;\n\n    for (int i = 0; i < pObjectPool->m_nSize; ++i)\n    {\n        if (pObjectPool->IsEmpty(i))\n            continue;\n\n        CObjectSAInterface* pObject = pObjectPool->GetObject(i);\n        if (pObject && pObject->sRefModelIndex == static_cast<short>(usModelID))\n        {\n            pObject->sRefModelIndex = -1;\n            pObject->bChangesVehColor = false;\n        }\n    }\n}\n\n//////////////////////////////////////////////////////////////////////////////////////////\n//                                       PEDS POOL                                      //\n''',
    "clean detached car-part references in object pool",
)


# ---------------------------------------------------------------------------
# DirectInput: valid count/flush queries may provide a null event buffer.
# Do not pass nullptr to memset while GUI input suppression is active.
# ---------------------------------------------------------------------------
replace_exact(
    "Client/core/DXHook/CProxyDirectInputDevice8.cpp",
    '''            // Clear strucutre(s).\n            memset(b, 0, a * (*c));\n            return hResult;\n''',
    '''            // Clear structure(s) only when the caller supplied an output buffer.\n            if (b)\n                memset(b, 0, a * (*c));\n            return hResult;\n''',
    "DirectInput null output-buffer guard",
)


# ---------------------------------------------------------------------------
# CEF shutdown: CefShutdown can freeze/crash during the instant quit path.
# The process is terminating anyway, so avoid full CEF teardown only there.
# ---------------------------------------------------------------------------
replace_exact(
    "Client/core/CCore.cpp",
    '''    m_bIsOfflineMod = false;\n    m_bQuitOnPulse = false;\n    m_bDestroyMessageBox = false;\n''',
    '''    m_bIsOfflineMod = false;\n    m_bQuitOnPulse = false;\n    m_bIsQuitting = false;\n    m_bDestroyMessageBox = false;\n''',
    "initialize instant-quit state",
)

replace_exact(
    "Client/core/CCore.cpp",
    '''void CCore::DestroyWeb()\n{\n    WriteDebugEvent("CCore::DestroyWeb");\n    SAFE_DELETE(m_pWebCore);\n    m_WebCoreModule.UnloadModule();\n}\n''',
    '''void CCore::DestroyWeb()\n{\n    WriteDebugEvent("CCore::DestroyWeb");\n    // CefShutdown can stall during the instant process-exit path. Normal teardown\n    // still performs full cleanup; instant quit lets process termination reclaim it.\n    if (!m_bIsQuitting)\n        SAFE_DELETE(m_pWebCore);\n    m_WebCoreModule.UnloadModule();\n}\n''',
    "avoid CEF shutdown freeze during instant quit",
)

replace_exact(
    "Client/core/CCore.cpp",
    '''void CCore::Quit(bool bInstantly)\n{\n    if (bInstantly)\n    {\n        AddReportLog(7101, "Core - Quit");\n''',
    '''void CCore::Quit(bool bInstantly)\n{\n    if (bInstantly)\n    {\n        m_bIsQuitting = true;\n        AddReportLog(7101, "Core - Quit");\n''',
    "mark instant quit before teardown",
)

replace_exact(
    "Client/core/CCore.h",
    '''    bool m_bQuitOnPulse;\n    bool m_bDestroyMessageBox;\n''',
    '''    bool m_bQuitOnPulse;\n    bool m_bIsQuitting;\n    bool m_bDestroyMessageBox;\n''',
    "store instant-quit state",
)


print("Somnis safe client backports pass 3:")
for label, path in changed:
    print(f"  APPLIED: {label} [{path}]")
for label, path in already:
    print(f"  ALREADY: {label} [{path}]")
for label, path, reason in skipped:
    print(f"  SKIPPED: {label} [{path}] - {reason}")
print(f"summary: applied={len(changed)} already={len(already)} skipped={len(skipped)}")
