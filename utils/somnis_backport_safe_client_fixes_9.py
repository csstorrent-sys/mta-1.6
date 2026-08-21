#!/usr/bin/env python3
"""Ninth Somnis MTA 1.6 protocol-neutral client stability pass.

Adds a bounded CreateFileW path for streaming IMG/archive files so a stalled
filesystem/AV/network filter cannot block the client forever while loading.
No network protocol or server compatibility code is modified.
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


path = "Client/game_sa/CStreamingSA.cpp"
p = Path(path)
if not p.exists():
    skipped.append(("bounded streaming archive open", path, "file missing"))
else:
    text = p.read_text(encoding="utf-8")

    if "CreateFileWithTimeoutForStreaming" in text:
        already.append(("bounded streaming archive open", path))
    else:
        include_old = '#include "processthreadsapi.h"\n'
        include_new = '#include "processthreadsapi.h"\n#include <atomic>\n'
        if include_old in text and '#include <atomic>\n' not in text:
            text = text.replace(include_old, include_new, 1)

        marker = '''namespace\n{\n    //\n    // Used in LoadAllRequestedModels to record state\n'''
        helper = r'''namespace
{
    namespace StreamingEventPool
    {
        constexpr auto SIZE = 8u;
        static std::atomic<HANDLE> slots[SIZE] = {};

        inline HANDLE Acquire()
        {
            for (auto& slot : slots)
            {
                if (auto h = slot.exchange(nullptr))
                {
                    ResetEvent(h);
                    return h;
                }
            }
            return CreateEventW(nullptr, TRUE, FALSE, nullptr);
        }

        inline void Release(HANDLE h)
        {
            if (!h)
                return;

            for (auto& slot : slots)
            {
                HANDLE expected = nullptr;
                if (slot.compare_exchange_strong(expected, h))
                    return;
            }
            CloseHandle(h);
        }
    }

    namespace AsyncStreamingFile
    {
        enum class State : int
        {
            Running = 0,
            Completed = 1,
            Abandoned = 2
        };

        constexpr DWORD TIMEOUT_MS = 5000;

        struct Params
        {
            std::wstring     fileName;
            DWORD            access = 0;
            DWORD            shareMode = 0;
            DWORD            disposition = 0;
            DWORD            flags = 0;
            HANDLE           result = INVALID_HANDLE_VALUE;
            DWORD            error = ERROR_SUCCESS;
            HANDLE           event = nullptr;
            std::atomic<int> state{0};
        };

        constexpr auto POOL_SIZE = 8u;
        static std::atomic<Params*> pool[POOL_SIZE] = {};

        inline Params* Acquire()
        {
            for (auto& slot : pool)
            {
                if (auto* p = slot.exchange(nullptr))
                {
                    p->state = static_cast<int>(State::Running);
                    p->result = INVALID_HANDLE_VALUE;
                    p->error = ERROR_SUCCESS;
                    p->event = nullptr;
                    p->fileName.clear();
                    return p;
                }
            }
            return new (std::nothrow) Params();
        }

        inline void Release(Params* p)
        {
            if (!p)
                return;

            p->fileName.clear();
            for (auto& slot : pool)
            {
                Params* expected = nullptr;
                if (slot.compare_exchange_strong(expected, p))
                    return;
            }
            delete p;
        }

        static DWORD WINAPI PoolCallback(LPVOID arg)
        {
            auto* p = static_cast<Params*>(arg);
            p->result = CreateFileW(p->fileName.c_str(), p->access, p->shareMode, nullptr, p->disposition, p->flags, nullptr);
            p->error = GetLastError();

            auto expected = static_cast<int>(State::Running);
            if (p->state.compare_exchange_strong(expected, static_cast<int>(State::Completed)))
            {
                SetEvent(p->event);
            }
            else
            {
                StreamingEventPool::Release(p->event);
                if (p->result != INVALID_HANDLE_VALUE)
                    CloseHandle(p->result);
                Release(p);
            }
            return 0;
        }
    }

    static HANDLE CreateFileWithTimeoutForStreaming(LPCWSTR lpFileName, DWORD dwDesiredAccess, DWORD dwShareMode, DWORD dwCreationDisposition,
                                                    DWORD dwFlagsAndAttributes)
    {
        using namespace AsyncStreamingFile;

        if (!lpFileName)
        {
            SetLastError(ERROR_INVALID_PARAMETER);
            return INVALID_HANDLE_VALUE;
        }

        auto* params = Acquire();
        if (!params)
            return INVALID_HANDLE_VALUE;

        try
        {
            params->fileName = lpFileName;
        }
        catch (...)
        {
            Release(params);
            return INVALID_HANDLE_VALUE;
        }

        HANDLE event = StreamingEventPool::Acquire();
        if (!event)
        {
            Release(params);
            return INVALID_HANDLE_VALUE;
        }

        params->access = dwDesiredAccess;
        params->shareMode = dwShareMode;
        params->disposition = dwCreationDisposition;
        params->flags = dwFlagsAndAttributes;
        params->event = event;

        if (!QueueUserWorkItem(PoolCallback, params, WT_EXECUTELONGFUNCTION))
        {
            StreamingEventPool::Release(event);
            Release(params);
            return CreateFileW(lpFileName, dwDesiredAccess, dwShareMode, nullptr, dwCreationDisposition, dwFlagsAndAttributes, nullptr);
        }

        if (WaitForSingleObject(event, TIMEOUT_MS) == WAIT_OBJECT_0)
        {
            StreamingEventPool::Release(event);
            HANDLE result = params->result;
            DWORD error = params->error;
            Release(params);
            SetLastError(error);
            return result;
        }

        AddReportLog(6213, SString("Streaming CreateFile timed out after %ums", TIMEOUT_MS));

        auto expected = static_cast<int>(State::Running);
        if (params->state.compare_exchange_strong(expected, static_cast<int>(State::Abandoned)))
        {
            SetLastError(ERROR_TIMEOUT);
        }
        else
        {
            StreamingEventPool::Release(event);
            if (params->result != INVALID_HANDLE_VALUE)
                CloseHandle(params->result);
            Release(params);
            SetLastError(ERROR_TIMEOUT);
        }
        return INVALID_HANDLE_VALUE;
    }

    //
    // Used in LoadAllRequestedModels to record state
'''

        if marker not in text:
            skipped.append(("bounded streaming archive open", path, "namespace insertion marker not found"))
        else:
            text = text.replace(marker, helper, 1)

            old_call = '''    // Create new stream handler\n    const auto streamCreateFlags = *(DWORD*)0x8E3FE0;\n    HANDLE     hFile = CreateFileW(szFilePath, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING,\n                               streamCreateFlags | FILE_ATTRIBUTE_READONLY | FILE_FLAG_RANDOM_ACCESS, NULL);\n'''
            new_call = '''    // Create new stream handler with a timeout so a stuck filesystem/AV filter cannot freeze the client forever.\n    const auto streamCreateFlags = *(DWORD*)0x8E3FE0;\n    HANDLE     hFile = CreateFileWithTimeoutForStreaming(szFilePath, GENERIC_READ, FILE_SHARE_READ, OPEN_EXISTING,\n                                                         streamCreateFlags | FILE_ATTRIBUTE_READONLY | FILE_FLAG_RANDOM_ACCESS);\n'''
            if old_call not in text:
                skipped.append(("bounded streaming archive open", path, "AddArchive CreateFileW pattern not found"))
            else:
                text = text.replace(old_call, new_call, 1)
                p.write_text(text, encoding="utf-8", newline="")
                changed.append(("bounded streaming archive open", path))

print("Somnis safe client backports pass 9:")
for label, path in changed:
    print(f"  APPLIED: {label} [{path}]")
for label, path in already:
    print(f"  ALREADY: {label} [{path}]")
for label, path, reason in skipped:
    print(f"  SKIPPED: {label} [{path}] - {reason}")
print(f"summary: applied={len(changed)} already={len(already)} skipped={len(skipped)}")
