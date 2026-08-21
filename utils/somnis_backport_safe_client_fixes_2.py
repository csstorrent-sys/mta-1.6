#!/usr/bin/env python3
"""Second Somnis MTA 1.6 client-only stability backport pass.

Only protocol-neutral client changes live here. No netcode version, packet layout,
RPC ID, bitstream format, server logic or compatibility identifier is modified.
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


# Entity-add packets can advertise more sirens than the client's fixed array owns.
# Consume every wire entry to keep the 1.6 bitstream aligned, but store only valid slots.
replace_exact(
    "Client/mods/deathmatch/logic/CPacketHandler.cpp",
    '''                            pVehicle->GiveVehicleSirens(ucSirenType, ucSirenCount);\n                            for (int i = 0; i < ucSirenCount; i++)\n                            {\n                                SVehicleSirenSync sirenData;\n                                bitStream.Read(&sirenData);\n                                pVehicle->SetVehicleSirenPosition(i, sirenData.data.m_vecSirenPositions);\n                                pVehicle->SetVehicleSirenColour(i, sirenData.data.m_colSirenColour);\n                                pVehicle->SetVehicleSirenMinimumAlpha(i, sirenData.data.m_dwSirenMinAlpha);\n''',
    '''                            const unsigned char ucStoredSirenCount = std::min<unsigned char>(ucSirenCount, SIREN_COUNT_MAX);\n                            pVehicle->GiveVehicleSirens(ucSirenType, ucStoredSirenCount);\n                            for (int i = 0; i < ucSirenCount; i++)\n                            {\n                                SVehicleSirenSync sirenData;\n                                bitStream.Read(&sirenData);\n                                if (i >= ucStoredSirenCount)\n                                    continue;\n                                pVehicle->SetVehicleSirenPosition(i, sirenData.data.m_vecSirenPositions);\n                                pVehicle->SetVehicleSirenColour(i, sirenData.data.m_colSirenColour);\n                                pVehicle->SetVehicleSirenMinimumAlpha(i, sirenData.data.m_dwSirenMinAlpha);\n''',
    "entity-add vehicle siren bounds",
)

# Body part tables contain exactly 10 entries (0..9). ID 10 previously hit a fixed-array assert.
replace_exact(
    "Client/mods/deathmatch/logic/CClientPed.cpp",
    '''const char* CClientPed::GetBodyPartName(unsigned char ucID)\n{\n    if (ucID <= 10)\n''',
    '''const char* CClientPed::GetBodyPartName(unsigned char ucID)\n{\n    if (ucID < 10)\n''',
    "client ped body-part bounds",
)
replace_exact(
    "Client/mods/deathmatch/logic/CStaticFunctionDefinitions.cpp",
    '''bool CStaticFunctionDefinitions::GetBodyPartName(unsigned char ucID, SString& strOutName)\n{\n    if (ucID <= 10)\n''',
    '''bool CStaticFunctionDefinitions::GetBodyPartName(unsigned char ucID, SString& strOutName)\n{\n    if (ucID < 10)\n''',
    "client body-part API bounds",
)

# engineFreeModel must not free model data while destroyElement() still has a native
# entity queued for deferred deletion. Flush those native deletions first.
replace_exact(
    "Client/mods/deathmatch/logic/luadefs/CLuaEngineDefs.cpp",
    '''    if (!argStream.HasErrors())\n    {\n        auto                          modelManager = m_pManager->GetModelManager();\n''',
    '''    if (!argStream.HasErrors())\n    {\n        // destroyElement only queues the native entity for deletion. Freeing the model\n        // in the same tick can leave GTA collision code touching already-freed model data.\n        g_pClientGame->GetElementDeleter()->DoDeleteAll();\n\n        auto                          modelManager = m_pManager->GetModelManager();\n''',
    "flush deferred elements before engineFreeModel",
)

# A copied weapon/clump vtable may read CClumpModelInfoSAInterface::m_nAnimFileIndex.
# Allocate/copy the full clump-sized interface instead of a smaller base object.
replace_exact(
    "Client/game_sa/CModelInfoSA.cpp",
    '''void CModelInfoSA::MakeObjectModel(ushort usBaseID)\n{\n    CBaseModelInfoSAInterface* m_pInterface = new CBaseModelInfoSAInterface();\n\n    CBaseModelInfoSAInterface* pBaseObjectInfo = ppModelInfo[usBaseID];\n    MemCpyFast(m_pInterface, pBaseObjectInfo, sizeof(CBaseModelInfoSAInterface));\n    m_pInterface->usNumberOfRefs = 0;\n    m_pInterface->pRwObject = nullptr;\n    m_pInterface->usUnknown = 65535;\n    m_pInterface->usDynamicIndex = 65535;\n''',
    '''void CModelInfoSA::MakeObjectModel(ushort usBaseID)\n{\n    CClumpModelInfoSAInterface* m_pInterface = new CClumpModelInfoSAInterface();\n\n    CBaseModelInfoSAInterface* pBaseObjectInfo = ppModelInfo[usBaseID];\n    MemCpyFast(m_pInterface, pBaseObjectInfo, sizeof(CClumpModelInfoSAInterface));\n    m_pInterface->usNumberOfRefs = 0;\n    m_pInterface->pRwObject = nullptr;\n    m_pInterface->usUnknown = 65535;\n    m_pInterface->usDynamicIndex = 65535;\n    m_pInterface->m_nAnimFileIndex = 0xFFFFFFFF;\n''',
    "weapon-based object model allocation bounds",
)

print("Somnis safe client backports pass 2:")
for label, path in changed:
    print(f"  APPLIED: {label} [{path}]")
for label, path in already:
    print(f"  ALREADY: {label} [{path}]")
for label, path, reason in skipped:
    print(f"  SKIPPED: {label} [{path}] - {reason}")
print(f"summary: applied={len(changed)} already={len(already)} skipped={len(skipped)}")
