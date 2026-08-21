/*****************************************************************************
 *
 *  PROJECT:     Multi Theft Auto v1.0
 *               (Shared logic for modifications)
 *  LICENSE:     See LICENSE in the top level directory
 *  FILE:        mods/shared_logic/CClientModelRequestManager.h
 *  PURPOSE:     Entity model streaming manager class
 *
 *****************************************************************************/

class CClientModelRequestManager;

#pragma once

#include "CClientCommon.h"
#include "CClientEntity.h"
#include <list>
#include <unordered_map>

struct SClientModelRequest
{
    CModelInfo*    pModel;
    CClientEntity* pEntity;
    CElapsedTime   requestTimer;
    unsigned char  ucRetryCount = 0;
};

class CClientModelRequestManager
{
    friend class CClientManager;

public:
    CClientModelRequestManager();
    ~CClientModelRequestManager();

    bool        IsLoaded(unsigned short usModelID);
    bool        IsRequested(CModelInfo* pModelInfo);
    bool        HasRequested(CClientEntity* pRequester);
    CModelInfo* GetRequestedModelInfo(CClientEntity* pRequester);

    bool RequestBlocking(unsigned short usModelID, const char* szTag);

    bool Request(unsigned short usModelID, CClientEntity* pRequester);
    void Cancel(CClientEntity* pRequester, bool bAllowQueue);

private:
    using RequestList = std::list<SClientModelRequest*>;
    using RequestIterator = RequestList::iterator;

    void DoPulse();
    bool GetRequestEntry(CClientEntity* pRequester, RequestIterator& iter);
    void RemoveRequestLookup(CClientEntity* pRequester);

    bool                                                m_bDoingPulse;
    RequestList                                         m_Requests;
    std::unordered_map<CClientEntity*, RequestIterator> m_RequestByEntity;
    std::list<CClientEntity*>                           m_CancelQueue;
};
