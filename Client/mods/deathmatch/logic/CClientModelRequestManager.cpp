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
    // Somnis: do not let a large batch of models all finish/retry in the same frame.
    // The original 1.6 loop can activate every ready model at once and can also re-request
    // every overdue model on the same pulse. On heavily modded RP servers that turns one
    // frame into a burst of DFF/TXD/model work and native entity creation.
    //
    // The upper limits preserve throughput on fast PCs. The actual per-frame budget is
    // reduced automatically when frame rate is already under pressure, so streaming work
    // cannot make a bad frame substantially worse.
    constexpr size_t SOMNIS_MAX_IMMEDIATE_LOADED_REQUESTS_PER_FRAME = 8;
    constexpr size_t SOMNIS_MAX_MODEL_COMPLETIONS_PER_PULSE = 8;
    constexpr size_t SOMNIS_MAX_MODEL_RETRIES_PER_PULSE = 16;

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
}

bool CClientModelRequestManager::IsLoaded(unsigned short usModelID)
{
    // Grab the model info
    CModelInfo* pInfo = g_pGame->GetModelInfo(usModelID);
    if (pInfo)
    {
        return pInfo->IsLoaded() ? true : false;
    }

    return false;
}

bool CClientModelRequestManager::IsRequested(CModelInfo* pModelInfo)
{
    // Model-level queries are uncommon compared with requester lookups. Keep this scan
    // simple while the hot entity path below uses O(1) lookup.
    std::list<SClientModelRequest*>::iterator iter = m_Requests.begin();
    for (; iter != m_Requests.end(); iter++)
    {
        if ((*iter)->pModel == pModelInfo)
        {
            return true;
        }
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
    // Grab the model info
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

    // Bad model ID probably.
    return false;
}

bool CClientModelRequestManager::Request(unsigned short usModelID, CClientEntity* pRequester)
{
    assert(pRequester);
    SClientModelRequest* pEntry;

    // Grab the model info for that model
    CModelInfo* pInfo = g_pGame->GetModelInfo(usModelID);
    if (pInfo)
    {
        // Has it already requested something?
        RequestIterator iter;
        if (GetRequestEntry(pRequester, iter))
        {
            // Get the entry
            pEntry = *iter;

            // The same model?
            if (pInfo == pEntry->pModel)
            {
                // He has to wait more for it
                return false;
            }
            else
            {
                // Remove the reference to the old model
                pEntry->pModel->RemoveRef();

                // Is it loaded?
                if (pInfo->IsLoaded())
                {
                    if (CanActivateLoadedModelImmediately())
                    {
                        // Delete it, remove it from the list and return true.
                        RemoveRequestLookup(pRequester);
                        delete pEntry;
                        m_Requests.erase(iter);

                        pInfo->MakeCustomModel();
                        return true;
                    }

                    // A large entity packet can reference many models that are already resident.
                    // Queue the excess instead of creating every native entity on this one frame.
                    pEntry->pModel = pInfo;
                    pEntry->requestTimer.SetMaxIncrement(500);
                    pEntry->requestTimer.Reset();
                    pInfo->ModelAddRef(NON_BLOCKING, "CClientModelRequestManager::Request deferred loaded");
                    return false;
                }
                else
                {
                    // If not loaded. Replace the model we're going to load.
                    // Also remember that we requested it now.
                    pEntry->pModel = pInfo;
                    pEntry->requestTimer.Reset();

                    // Start loading the new model.
                    pInfo->ModelAddRef(NON_BLOCKING, "CClientModelRequestManager::Request");

                    // He has to wait for it.
                    return false;
                }
            }
        }
        else
        {
            // Already loaded? Usually return immediately, but during a burst defer excess
            // native entity creation to the normal model-request callback queue.
            if (pInfo->IsLoaded() && CanActivateLoadedModelImmediately())
            {
                pInfo->MakeCustomModel();
                return true;
            }

            // Hold a reference while the request waits in our queue. This is also used for
            // already-loaded models that are intentionally being activated progressively.
            pInfo->ModelAddRef(NON_BLOCKING, "CClientModelRequestManager::Request #2");

            // Add him to the list over models we're waiting for.
            pEntry = new SClientModelRequest;
            pEntry->pModel = pInfo;
            pEntry->pEntity = pRequester;
            pEntry->requestTimer.SetMaxIncrement(500);
            pEntry->requestTimer.Reset();
            m_Requests.push_back(pEntry);

            RequestIterator newIter = m_Requests.end();
            --newIter;
            m_RequestByEntity[pRequester] = newIter;

            // Return false. Caller needs to wait.
            return false;
        }
    }

    // Error, model is bad. Caller should not do this.
    return false;
}

void CClientModelRequestManager::Cancel(CClientEntity* pEntity, bool bAllowQueue)
{
    assert(pEntity);
    // Check to ensure entity has not got its knickers in a twist
    if (ListContains(m_CancelQueue, pEntity))
        return;

    // Are we inside a pulse? Add it to a list to delete after or we'll crash.
    // If not, cancel now.
    if (m_bDoingPulse)
    {
        // Check queuing is allowed by the caller
        assert(bAllowQueue);
        m_CancelQueue.push_back(pEntity);
    }
    else
    {
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
}

void CClientModelRequestManager::DoPulse()
{
    // Any requests?
    if (m_Requests.size() > 0)
    {
        // We are now doing the pulse
        m_bDoingPulse = true;

        const size_t uiCompletionBudget = GetSomnisCompletionBudget();
        const size_t uiRetryBudget = GetSomnisRetryBudget();
        size_t       uiCompletedThisPulse = 0;
        size_t       uiRetriedThisPulse = 0;

        // Call callbacks for finished models, but deliberately pace the expensive
        // MakeCustomModel/native entity creation work over several rendered frames.
        SClientModelRequest* pEntry;
        RequestIterator      iter;
        for (iter = m_Requests.begin(); iter != m_Requests.end();)
        {
            pEntry = *iter;

            // Is it loaded?
            if (pEntry->pModel->IsLoaded())
            {
                // Copy then remove from the list because the request is complete and we don't want it modified in Request()
                const SClientModelRequest entryCopy = *pEntry;
                RemoveRequestLookup(entryCopy.pEntity);
                delete pEntry;
                m_Requests.erase(iter);

                // Make sure custom things are replaced
                entryCopy.pModel->MakeCustomModel();

                // Create ped/object/vehicle using the loaded model (this can eventually trigger script events)
                entryCopy.pEntity->ModelRequestCallback(entryCopy.pModel);

                // Unreference us from the model (callback should've added a reference!)
                entryCopy.pModel->RemoveRef();

                ++uiCompletedThisPulse;
                if (uiCompletedThisPulse >= uiCompletionBudget)
                    break;

                // Restart loop because m_Requests may have been changed
                iter = m_Requests.begin();
            }
            else
            {
                // Been more than 2 seconds since we requested it? Request it again.
                if (pEntry->requestTimer.Get() > 2000 && uiRetriedThisPulse < uiRetryBudget)
                {
                    // Preserve MTA's explicit async/suspend semantics. When async is available,
                    // use the non-blocking request path; when a script/core explicitly suspended
                    // it, retain the original 1.6 blocking behavior instead of bypassing safety.
                    if (g_pGame->IsASyncLoadingEnabled())
                        pEntry->pModel->Request(NON_BLOCKING, "CClientModelRequestManager::DoPulse #1");
                    else
                        pEntry->pModel->Request(BLOCKING, "CClientModelRequestManager::DoPulse #2");

                    // Remember now as the time we requested it.
                    pEntry->requestTimer.Reset();
                    ++uiRetriedThisPulse;
                }

                // Increment iterator
                ++iter;
            }
        }

        // No longer doing the pulse
        m_bDoingPulse = false;

        // Cancel what we've scheduled for cancel now if anything
        if (m_CancelQueue.size() > 0)
        {
            // Cancel every entity in our cancel list
            list<CClientEntity*> cancelQueueCopy = m_CancelQueue;
            m_CancelQueue.clear();

            list<CClientEntity*>::iterator iter = cancelQueueCopy.begin();
            for (; iter != cancelQueueCopy.end(); ++iter)
            {
                Cancel(*iter, false);
            }
        }
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
