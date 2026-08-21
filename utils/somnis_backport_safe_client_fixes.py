#!/usr/bin/env python3
"""Apply Somnis' protocol-neutral MTA 1.6 client stability backports.

This intentionally changes client validation/stability only. It must not touch
netcode versions, packet layouts, protocol IDs or server-side behaviour.
The script is idempotent so it can safely run again after later rebases.
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


# Streamed-out peds are still valid Lua elements but have no native task manager.
for function_name, return_expr in (
    ("GetPedAnimationProgress", "return animAssociation->GetCurrentProgress() / animAssociation->GetLength();"),
    ("GetPedAnimationSpeed", "return animAssociation->GetCurrentSpeed();"),
    ("GetPedAnimationLength", "return animAssociation->GetLength();"),
):
    old_head = f'''float CLuaPedDefs::{function_name}(CClientPed* ped)\n{{\n    CTask*       currentTask = ped->GetTaskManager()->GetActiveTask();\n    std::int32_t type = currentTask->GetTaskType();\n\n    // check if animation (task type is 401)\n    if (type != 401)\n        return -1.0f;\n'''
    new_head = f'''float CLuaPedDefs::{function_name}(CClientPed* ped)\n{{\n    CTaskManager* taskManager = ped->GetTaskManager();\n    if (!taskManager)\n        return -1.0f;\n\n    CTask* currentTask = taskManager->GetActiveTask();\n    if (!currentTask || currentTask->GetTaskType() != TASK_SIMPLE_NAMED_ANIM)\n        return -1.0f;\n'''
    replace_exact(
        "Client/mods/deathmatch/logic/luadefs/CLuaPedDefs.cpp",
        old_head,
        new_head,
        f"streamed-out ped {function_name}",
    )

# Throw exception objects, not pointers, so the Lua parser can catch them.
replace_exact(
    "Client/mods/deathmatch/logic/luadefs/CLuaVehicleDefs.cpp",
    '        throw new std::invalid_argument("Invalid track number range (0-3)");',
    '        throw std::invalid_argument("Invalid track number range (0-3)");',
    "invalid train track exception",
)

# Prevent 32-bit multiplication wrap from accepting undersized dxSetTexturePixels buffers.
replace_exact(
    "Client/core/Graphics/CPixelsManager.cpp",
    "        uint uiPlainByteSize = uiOutWidth * uiOutHeight * 4 + SIZEOF_PLAIN_TAIL;",
    "        const uint64_t uiPlainByteSize = static_cast<uint64_t>(uiOutWidth) * static_cast<uint64_t>(uiOutHeight) * 4ULL + SIZEOF_PLAIN_TAIL;",
    "plain texture byte-size overflow",
)

# Reject impossible FFT band counts before allocation/indexing.
replace_exact(
    "Client/mods/deathmatch/logic/CStaticFunctionDefinitions.cpp",
    '''float* CStaticFunctionDefinitions::GetSoundFFTData(CClientSound& Sound, int iLength, int iBands)\n{\n    // Get our FFT Data\n''',
    '''static bool IsValidFFTBandCount(int iLength, int iBands)\n{\n    // BASS exposes half of the FFT sample count as usable spectrum bins.\n    return iLength > 0 && iBands >= 0 && iBands <= iLength / 2;\n}\n\nfloat* CStaticFunctionDefinitions::GetSoundFFTData(CClientSound& Sound, int iLength, int iBands)\n{\n    if (!IsValidFFTBandCount(iLength, iBands))\n        return nullptr;\n\n    // Get our FFT Data\n''',
    "sound FFT band validation",
)
replace_exact(
    "Client/mods/deathmatch/logic/CStaticFunctionDefinitions.cpp",
    '''float* CStaticFunctionDefinitions::GetSoundFFTData(CClientPlayer& Player, int iLength, int iBands)\n{\n    CClientPlayerVoice* pVoice = Player.GetVoice();\n''',
    '''float* CStaticFunctionDefinitions::GetSoundFFTData(CClientPlayer& Player, int iLength, int iBands)\n{\n    if (!IsValidFFTBandCount(iLength, iBands))\n        return nullptr;\n\n    CClientPlayerVoice* pVoice = Player.GetVoice();\n''',
    "voice FFT band validation",
)

# Validate COL entry sizes before handing malformed data to GTA's unchecked parser.
replace_exact(
    "Client/game_sa/CRenderWareSA.cpp",
    '''    // Load the col model\n    if (header.version[0] == 'C' && header.version[1] == 'O' && header.version[2] == 'L')\n    {\n        unsigned char* pModelData = (unsigned char*)buffer.data() + sizeof(ColModelFileHeader);\n''',
    '''    // Load the col model\n    if (header.version[0] == 'C' && header.version[1] == 'O' && header.version[2] == 'L')\n    {\n        constexpr DWORD COL_FILE_INFO_SIZE = sizeof(header.version) + sizeof(header.size);\n        constexpr DWORD COL_MODEL_NAME_SIZE = sizeof(header.name);\n        constexpr DWORD GTA_COL2_HEADER_SIZE = 0x4C;\n        constexpr DWORD GTA_COL3_HEADER_SIZE = 0x58;\n\n        const uint64_t totalSize = static_cast<uint64_t>(header.size) + COL_FILE_INFO_SIZE;\n        if (header.size < COL_MODEL_NAME_SIZE || totalSize > buffer.size())\n            return NULL;\n\n        const DWORD dataSize = header.size - COL_MODEL_NAME_SIZE;\n        if ((header.version[3] == '2' && dataSize < GTA_COL2_HEADER_SIZE) ||\n            (header.version[3] == '3' && dataSize < GTA_COL3_HEADER_SIZE))\n            return NULL;\n\n        unsigned char* pModelData = (unsigned char*)buffer.data() + sizeof(ColModelFileHeader);\n''',
    "COL header size validation",
)
replace_exact(
    "Client/game_sa/CRenderWareSA.cpp",
    "            LoadCollisionModelVer2(pModelData, header.size - 0x18, pColModel->GetInterface(), NULL);",
    "            LoadCollisionModelVer2(pModelData, dataSize, pColModel->GetInterface(), NULL);",
    "COL2 validated data size",
)
replace_exact(
    "Client/game_sa/CRenderWareSA.cpp",
    "            LoadCollisionModelVer3(pModelData, header.size - 0x18, pColModel->GetInterface(), NULL);",
    "            LoadCollisionModelVer3(pModelData, dataSize, pColModel->GetInterface(), NULL);",
    "COL3 validated data size",
)

# Vehicle sirens use a fixed array. Keep packet consumption aligned, but never write past it.
replace_exact(
    "Client/mods/deathmatch/logic/CClientVehicle.cpp",
    "    for (unsigned char i = 0; i < 7; i++)",
    "    for (unsigned char i = 0; i < SIREN_COUNT_MAX; i++)",
    "clear every vehicle siren slot",
)
replace_exact(
    "Client/mods/deathmatch/logic/CPacketHandler.cpp",
    '''                        pVehicle->GiveVehicleSirens(ucSirenType, ucSirenCount);\n                        for (unsigned char i = 0; i < ucSirenCount; i++)\n                        {\n                            SVehicleSirenSync sirenData;\n                            bitStream.Read(&sirenData);\n                            pVehicle->SetVehicleSirenPosition(i, sirenData.data.m_vecSirenPositions);\n''',
    '''                        // Count is read from the wire but indexes a fixed local array.\n                        // Consume every advertised entry to preserve stream alignment and only store valid slots.\n                        const unsigned char ucStoredSirenCount = std::min<unsigned char>(ucSirenCount, SIREN_COUNT_MAX);\n                        pVehicle->GiveVehicleSirens(ucSirenType, ucStoredSirenCount);\n                        for (unsigned char i = 0; i < ucSirenCount; i++)\n                        {\n                            SVehicleSirenSync sirenData;\n                            bitStream.Read(&sirenData);\n                            if (i >= ucStoredSirenCount)\n                                continue;\n                            pVehicle->SetVehicleSirenPosition(i, sirenData.data.m_vecSirenPositions);\n''',
    "entity-add vehicle siren bounds",
)
replace_exact(
    "Shared/sdk/net/SyncStructures.h",
    '''                bitStream.ReadBit(data.m_bDoLOSCheck);\n                bitStream.ReadBit(data.m_bUseRandomiser);\n                bitStream.ReadBit(data.m_bEnableSilent);\n                return true;\n''',
    '''                bitStream.ReadBit(data.m_bDoLOSCheck);\n                bitStream.ReadBit(data.m_bUseRandomiser);\n                bitStream.ReadBit(data.m_bEnableSilent);\n\n                // This ID indexes the client's fixed siren array. Read the whole structure first\n                // so malformed data cannot desynchronise the rest of the packet.\n                return data.m_ucSirenID <= SIREN_ID_MAX;\n''',
    "vehicle siren sync ID validation",
)

# engineFreeModel must reject negative IDs before model-array lookup.
replace_exact(
    "Client/mods/deathmatch/logic/luadefs/CLuaEngineDefs.cpp",
    '''    if (!argStream.HasErrors())\n    {\n        std::shared_ptr<CClientModel> pModel = m_pManager->GetModelManager()->FindModelByID(iModelID);\n''',
    '''    if (!argStream.HasErrors())\n    {\n        if (iModelID < 0)\n        {\n            lua_pushboolean(luaVM, false);\n            return 1;\n        }\n\n        std::shared_ptr<CClientModel> pModel = m_pManager->GetModelManager()->FindModelByID(iModelID);\n''',
    "engineFreeModel negative ID guard",
)

# Oversized TXD IDs must not wrap when converted to an internal model ID, and removal
# must go through the manager so model references/entities and the slot are cleaned.
replace_exact(
    "Client/mods/deathmatch/logic/luadefs/CLuaEngineDefs.cpp",
    '''bool CLuaEngineDefs::EngineFreeTXD(uint txdID)\n{\n    std::shared_ptr<CClientModel> pModel = m_pManager->GetModelManager()->FindModelByID(MAX_MODEL_DFF_ID + txdID);\n    return pModel && pModel->Deallocate();\n}\n''',
    '''bool CLuaEngineDefs::EngineFreeTXD(uint txdID)\n{\n    const std::uint32_t uiBaseIdForCol = g_pGame->GetBaseIDforCOL();\n    if (uiBaseIdForCol <= MAX_MODEL_DFF_ID || txdID >= uiBaseIdForCol - MAX_MODEL_DFF_ID)\n        return false;\n\n    const int iModelID = MAX_MODEL_DFF_ID + static_cast<int>(txdID);\n    auto modelManager = m_pManager->GetModelManager();\n    std::shared_ptr<CClientModel> pModel = modelManager->FindModelByID(iModelID);\n    return pModel && modelManager->Remove(pModel);\n}\n''',
    "engineFreeTXD overflow and leak guard",
)

# Reject stale/unallocated TXD pool IDs before assigning them to a model.
replace_exact(
    "Client/mods/deathmatch/logic/luadefs/CLuaEngineDefs.cpp",
    '''    if (uiModelID >= g_pGame->GetBaseIDforTXD() || !pModelInfo)\n        throw std::invalid_argument("Expected a valid model ID at argument 1");\n\n    pModelInfo->SetTextureDictionaryID(usTxdId);\n''',
    '''    if (uiModelID >= g_pGame->GetBaseIDforTXD() || !pModelInfo)\n        throw std::invalid_argument("Expected a valid model ID at argument 1");\n\n    if (g_pGame->GetPools()->GetTxdPool().IsFreeTextureDictonarySlot(usTxdId))\n        throw std::invalid_argument("Expected an allocated TXD ID at argument 2");\n\n    pModelInfo->SetTextureDictionaryID(usTxdId);\n''',
    "engineSetModelTXDID allocated-slot guard",
)

print("Somnis safe client backports:")
for label, path in changed:
    print(f"  APPLIED: {label} [{path}]")
for label, path in already:
    print(f"  ALREADY: {label} [{path}]")
for label, path, reason in skipped:
    print(f"  SKIPPED: {label} [{path}] - {reason}")
print(f"summary: applied={len(changed)} already={len(already)} skipped={len(skipped)}")
