/*****************************************************************************
 *
 *  PROJECT:     Multi Theft Auto v1.0
 *               (Shared logic for modifications)
 *  LICENSE:     See LICENSE in the top level directory
 *  FILE:        mods/shared_logic/CClientModelRequestManager.cpp
 *  PURPOSE:     Entity model streaming manager class
 *
 *****************************************************************************/

#include "StdInc.h"

using std::list;

namespace
{
    // Somnis: do not let a large batch of models all finish/retry/cancel in the same frame.
    // The original 1.6 loop can activate every ready model at once and can also re-request
    // every overdue model on the same pulse. On heavily modded RP servers that turns one
    // frame into a burst of DFF/TXD/model work and native entity creation/destruction.
    constexpr size_t SOMNIS_MAX_IMMEDIATE_LOADED_REQUESTS_PER_FRAME = 8;
    constexpr size_t SOMNIS_MAX_MODEL_COMPLETIONS_PER_PULSE = 8;
    constexpr size_t SOMNIS_MAX_MODEL_RETRIES_PER_PULSE = 16;
    constexpr size_t SOMNIS_MAX_MODEL_CANCELS_PER_PULSE = 64;
    constexpr unsigned long SOMNIS_BASE_MODEL_RETRY_MS = 2000;
    constexpr unsigned long SOMNIS_MAX_MODEL_RETRY_MS = 8000;

    size_t GetSomnisCompletionBudget()
    {
        if (!g_pGame)
            return SOMNIS_MAX_MODEL_COMPLETIONS_PER_PULSE;

        const float fFPS = g_pGame->GetFPS();
        if (fFPS > 0.0f && fFPS < 25.0f)
            return 2;
        if (fFPS > 0.0f && fFPS < 40.0f)
            return 4;
        return SOMNIS_MAX_MODEL_COMPLETIONS_PER_PULSE;
    }

    size_t GetSomnisRetryBudget()
    {
        if (!g_pGame)
            return SOMNIS_MAX_MODEL_RETRIES_PER_PULSE;

        const float fFPS = g_pGame->GetFPS();
        if (fFPS > 0.0f && fFPS < 25.0f)
            return 4;
        if (fFPS > 0.0f && fFPS < 40.0f)
            return 8;
        return SOMNIS_MAX_MODEL_RETRIES_PER_PULSE;
    }

    size_t GetSomnisCancelBudget()
    {
        if (!g_pGame)
            return SOMNIS_MAX_MODEL_CANCELS_PER_PULSE;

        const float fFPS = g_pGame->GetFPS();
        if (fFPS > 0.0f && fFPS < 25.0f)
            return 16;
        if (fFPS > 0.0f && fFPS < 40.0f)
            return 32;
        return SOMNIS_MAX_MODEL_CANCELS_PER_PULSE;
    }

    TIMEUS GetSomnisPulseTimeBudgetUs()
    {
        if (!g_pGame)
            return 4000;

        const float fFPS = g_pGame->GetFPS();
        if (fFPS > 0.0f && fFPS < 25.0f)
            return 1500;
        if (fFPS > 0.0f && fFPS < 40.0f)
            return 2500;
        return 4000;
    }

    unsigned long GetSomnisRetryDelayMs(unsigned char ucRetryCount)
    {
        const unsigned int uiShift = std::min<unsigned int>(ucRetryCount, 2U);
        return std::min<unsigned long>(SOMNIS_BASE_MODEL_RETRY_MS << uiShift, SOMNIS_MAX_MODEL_RETRY_MS);
    }

    size_t GetSomnisImmediateBudget()
    {
        return std::min(SOMNIS_MAX_IMMEDIATE_LOADED_REQUESTS_PER_FRAME, GetSomnisCompletionBudget());
    }

    bool CanActivateLoadedModelImmediately()
    {
        static int    s_iLastFrame = -1;
        static size_t s_uiActivatedThisFrame = 0;

        const int iCurrentFrame = g_pGame ? g_pGame->GetSystemFrameCounter() : 0;
        if (iCurrentFrame != s_iLastFrame)
        {
            s_iLastFrame = iCurrentFrame;
            s_uiActivatedThisFrame = 0;
        }

        if (s_uiActivatedThisFrame >= GetSomnisImmediateBudget())
            return false;

        ++s_uiActivatedThisFrame;
        return true;
    }
}

CClientModelRequestManager::CClientModelRequestManager()
{
    m_bDoingPulse = false;

    // A busy RP server can enqueue hundreds of model requests while entering a dense
    // area. Reserve lookup space once so the client does not repeatedly rehash while
    // the async streamer is already under load.
    m_RequestByEntity.reserve(256);
    m_CancelQueuedEntities.reserve(256);
}

CClientModelRequestManager::~CClientModelRequestManager()
{
    // Delete all our requests.
    list<SClientModelRequest*>::iterator iter;
    for (iter = m_Requests.begin(); iter != m_Requests.end(); iter++)
    {
        delete *iter;
    }

    m_Requests.clear();
    m_RequestByEntity.clear();
    m_CancelQueue.clear();
    m_CancelQueuedEntities.clear();
}

bool CClientModelRequestManager::IsLoaded(unsigned short usModelID)
{
    CModelInfo* pInfo = g_pGame->GetModelInfo(usModelID);
    if (pInfo)
        return pInfo->IsLoaded() ? true : false;

    return false;
}

bool CClientModelRequestManager::IsRequested(CModelInfo* pModelInfo)
{
    std::list<SClientModelRequest*>::iterator iter = m_Requests.begin();
    for (; iter != m_Requests.end(); iter++)
    {
        if ((*iter)->pModel == pModelInfo)
            return true;
    }

    return false;
}

bool CClientModelRequestManager::HasRequested(CClientEntity* pRequester)
{
    assert(pRequester);
    return m_RequestByEntity.find(pRequester) != m_RequestByEntity.end();
}

CModelInfo* CClientModelRequestManager::GetRequestedModelInfo(CClientEntity* pRequester)
{
    assert(pRequester);

    const auto iterLookup = m_RequestByEntity.find(pRequester);
    if (iterLookup == m_RequestByEntity.end())
        return NULL;

    const RequestIterator iterRequest = iterLookup->second;
    if (iterRequest == m_Requests.end() || !*iterRequest)
        return NULL;

    return (*iterRequest)->pModel;
}

bool CClientModelRequestManager::RequestBlocking(unsigned short usModelID, const char* szTag)
{
    CModelInfo* pInfo = g_pGame->GetModelInfo(usModelID);
    if (pInfo)
    {
        pInfo->Request(BLOCKING, szTag);
        if (pInfo->IsLoaded())
        {
            pInfo->MakeCustomModel();
            return true;
        }
        OutputDebugLine(SString("[Models] RequestBlocking failed for id %d", usModelID));
    }

    return false;
}

bool CClientModelRequestManager::Request(unsigned short usModelID, CClientEntity* pRequester)
{
    assert(pRequester);
    SClientModelRequest* pEntry;

    CModelInfo* pInfo = g_pGame->GetModelInfo(usModelID);
    if (pInfo)
    {
        RequestIterator iter;
        if (GetRequestEntry(pRequester, iter))
        {
            pEntry = *iter;

            if (pInfo == pEntry->pModel)
                return false;

            pEntry->pModel->RemoveRef();

            if (pInfo->IsLoaded())
            {
                if (CanActivateLoadedModelImmediately())
                {
                    RemoveRequestLookup(pRequester);
                    delete pEntry;
                    m_Requests.erase(iter);

                    pInfo->MakeCustomModel();
                    return true;
                }

                pEntry->pModel = pInfo;
                pEntry->ucRetryCount = 0;
                pEntry->requestTimer.SetMaxIncrement(500);
                pEntry->requestTimer.Reset();
                pInfo->ModelAddRef(NON_BLOCKING, "CClientModelRequestManager::Request deferred loaded");
                return false;
            }

            pEntry->pModel = pInfo;
            pEntry->ucRetryCount = 0;
            pEntry->requestTimer.Reset();
            pInfo->ModelAddRef(NON_BLOCKING, "CClientModelRequestManager::Request");
            return false;
        }

        if (pInfo->IsLoaded() && CanActivateLoadedModelImmediately())
        {
            pInfo->MakeCustomModel();
            return true;
        }

        pInfo->ModelAddRef(NON_BLOCKING, "CClientModelRequestManager::Request #2");

        pEntry = new SClientModelRequest;
        pEntry->pModel = pInfo;
        pEntry->pEntity = pRequester;
        pEntry->ucRetryCount = 0;
        pEntry->requestTimer.SetMaxIncrement(500);
        pEntry->requestTimer.Reset();
        m_Requests.push_back(pEntry);

        RequestIterator newIter = m_Requests.end();
        --newIter;
        m_RequestByEntity[pRequester] = newIter;
        return false;
    }

    return false;
}

void CClientModelRequestManager::Cancel(CClientEntity* pEntity, bool bAllowQueue)
{
    assert(pEntity);

    if (m_CancelQueuedEntities.find(pEntity) != m_CancelQueuedEntities.end())
        return;

    // During callbacks the request list may be actively mutated. Defer the cancellation,
    // but keep an O(1) set beside the list so a mass stream-out cannot turn duplicate
    // checks into an O(n²) frame spike.
    if (m_bDoingPulse)
    {
        assert(bAllowQueue);
        m_CancelQueue.push_back(pEntity);
        m_CancelQueuedEntities.insert(pEntity);
        return;
    }

    RequestIterator iter;
    if (GetRequestEntry(pEntity, iter))
    {
        SClientModelRequest* pEntry = *iter;
        pEntry->pModel->RemoveRef();

        RemoveRequestLookup(pEntity);
        delete pEntry;
        m_Requests.erase(iter);
    }
}

void CClientModelRequestManager::DoPulse()
{
    if (m_Requests.empty() && m_CancelQueue.empty())
        return;

    m_bDoingPulse = true;

    const size_t uiCompletionBudget = GetSomnisCompletionBudget();
    const size_t uiRetryBudget = GetSomnisRetryBudget();
    const TIMEUS uiTimeBudgetUs = GetSomnisPulseTimeBudgetUs();
    const TIMEUS uiPulseStartUs = GetTimeUs();
    size_t       uiCompletedThisPulse = 0;
    size_t       uiRetriedThisPulse = 0;

    SClientModelRequest* pEntry;
    RequestIterator      iter;
    for (iter = m_Requests.begin(); iter != m_Requests.end();)
    {
        pEntry = *iter;

        if (pEntry->pModel->IsLoaded())
        {
            const SClientModelRequest entryCopy = *pEntry;
            RemoveRequestLookup(entryCopy.pEntity);
            delete pEntry;
            m_Requests.erase(iter);

            entryCopy.pModel->MakeCustomModel();
            entryCopy.pEntity->ModelRequestCallback(entryCopy.pModel);
            entryCopy.pModel->RemoveRef();

            ++uiCompletedThisPulse;
            if (uiCompletedThisPulse >= uiCompletionBudget || GetTimeUs() - uiPulseStartUs >= uiTimeBudgetUs)
                break;

            // Callback code is allowed to mutate the request list, so restart safely.
            iter = m_Requests.begin();
        }
        else
        {
            const unsigned long ulRetryDelay = GetSomnisRetryDelayMs(pEntry->ucRetryCount);
            if (pEntry->requestTimer.Get() > ulRetryDelay && uiRetriedThisPulse < uiRetryBudget)
            {
                bool bDidRetry = false;

                if (g_pGame->IsASyncLoadingEnabled())
                {
                    pEntry->pModel->Request(NON_BLOCKING, "CClientModelRequestManager::DoPulse #1");
                    bDidRetry = true;
                }
                else if (g_pGame->IsASyncLoadingEnabled(true))
                {
                    // Async is configured but temporarily suspended by the core/game.
                    // Never turn that safety suspension into a blocking main-thread load.
                }
                else
                {
                    // Explicit script-side async disable keeps original 1.6 behaviour.
                    pEntry->pModel->Request(BLOCKING, "CClientModelRequestManager::DoPulse #2");
                    bDidRetry = true;
                }

                if (bDidRetry)
                {
                    pEntry->requestTimer.Reset();
                    if (pEntry->ucRetryCount < 3)
                        ++pEntry->ucRetryCount;
                    ++uiRetriedThisPulse;

                    if (GetTimeUs() - uiPulseStartUs >= uiTimeBudgetUs)
                        break;
                }
            }

            ++iter;
        }
    }

    m_bDoingPulse = false;

    // A large sector unload can schedule hundreds of request cancellations at once.
    // Destroying them all after one callback defeats the progressive loader, so drain
    // the queue with the same FPS/time aware approach and leave the rest for next pulse.
    const size_t uiCancelBudget = GetSomnisCancelBudget();
    size_t       uiCancelledThisPulse = 0;
    while (!m_CancelQueue.empty() && uiCancelledThisPulse < uiCancelBudget)
    {
        CClientEntity* pEntity = m_CancelQueue.front();
        m_CancelQueue.pop_front();
        m_CancelQueuedEntities.erase(pEntity);
        Cancel(pEntity, false);
        ++uiCancelledThisPulse;

        if (GetTimeUs() - uiPulseStartUs >= uiTimeBudgetUs)
            break;
    }
}

bool CClientModelRequestManager::GetRequestEntry(CClientEntity* pRequester, RequestIterator& iterOut)
{
    const auto iterLookup = m_RequestByEntity.find(pRequester);
    if (iterLookup == m_RequestByEntity.end())
        return false;

    iterOut = iterLookup->second;
    return iterOut != m_Requests.end();
}

void CClientModelRequestManager::RemoveRequestLookup(CClientEntity* pRequester)
{
    if (pRequester)
        m_RequestByEntity.erase(pRequester);
}
