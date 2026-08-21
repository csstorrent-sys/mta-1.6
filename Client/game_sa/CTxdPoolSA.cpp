/*****************************************************************************
 *
 *  PROJECT:     Multi Theft Auto
 *  LICENSE:     See LICENSE in the top level directory
 *
 *  Multi Theft Auto is available from https://www.multitheftauto.com/
 *
 *****************************************************************************/

#include "StdInc.h"
#include "CTxdPoolSA.h"
#include "CGameSA.h"
#include "CKeyGenSA.h"

extern CGameSA* pGame;

CTxdPoolSA::CTxdPoolSA()
{
    m_ppTxdPoolInterface = (CPoolSAInterface<CTextureDictonarySAInterface>**)0xC8800C;
}

std::uint32_t CTxdPoolSA::AllocateTextureDictonarySlot(std::uint32_t uiSlotId, std::string& strTxdName)
{
    CPoolSAInterface<CTextureDictonarySAInterface>* pPool = m_ppTxdPoolInterface ? *m_ppTxdPoolInterface : nullptr;
    if (!pPool || pPool->IsContains(uiSlotId))
        return static_cast<std::uint32_t>(-1);

    CTextureDictonarySAInterface* pTxd = pPool->AllocateAt(uiSlotId);
    if (!pTxd)
        return static_cast<std::uint32_t>(-1);

    strTxdName.resize(24);

    pTxd->usUsagesCount = 0;
    pTxd->hash = pGame->GetKeyGen()->GetUppercaseKey(strTxdName.c_str());
    pTxd->rwTexDictonary = nullptr;
    pTxd->usParentIndex = -1;

    return pPool->GetObjectIndex(pTxd);
}

void CTxdPoolSA::RemoveTextureDictonarySlot(std::uint32_t uiTxdId)
{
    CPoolSAInterface<CTextureDictonarySAInterface>* pPool = m_ppTxdPoolInterface ? *m_ppTxdPoolInterface : nullptr;
    if (!pPool || !pPool->IsContains(uiTxdId))
        return;

    typedef std::uint32_t(__cdecl * Function_TxdReleaseSlot)(std::uint32_t uiTxdId);
    ((Function_TxdReleaseSlot)(0x731E90))(uiTxdId);

    pPool->Release(uiTxdId);
}

bool CTxdPoolSA::IsFreeTextureDictonarySlot(std::uint32_t uiTxdId)
{
    CPoolSAInterface<CTextureDictonarySAInterface>* pTxdPool = m_ppTxdPoolInterface ? *m_ppTxdPoolInterface : nullptr;

    // IsEmpty assumes the index is valid. Script/resource cleanup can retain stale
    // TXD IDs after restarts, so use the pool's bounds-aware containment check.
    return !pTxdPool || !pTxdPool->IsContains(uiTxdId);
}

std::uint16_t CTxdPoolSA::GetFreeTextureDictonarySlot()
{
    CPoolSAInterface<CTextureDictonarySAInterface>* pPool = m_ppTxdPoolInterface ? *m_ppTxdPoolInterface : nullptr;
    return pPool ? pPool->GetFreeSlot() : static_cast<std::uint16_t>(-1);
}
