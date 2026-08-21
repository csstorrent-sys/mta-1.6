#!/usr/bin/env python3
"""Sixth Somnis MTA 1.6 protocol-neutral client stability pass.

Hardens CEGUI static-image and web-browser widgets against failed browser/texture
creation and stale CEGUI references. No network or protocol code is modified.
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


# Static images: reject null textures, detach stale widget/imageset references
# before replacing an owned texture and catch CEGUI allocation/load failures.
replace_exact(
    "Client/gui/CGUIStaticImage_Impl.cpp",
    '''bool CGUIStaticImage_Impl::LoadFromTexture(CGUITexture* pTexture)\n{\n    if (m_pImageset && m_pImage)\n    {\n        m_pImageset->undefineAllImages();\n    }\n\n    if (m_pTexture && pTexture != m_pTexture)\n    {\n        if (m_bCreatedTexture)\n        {\n            delete m_pTexture;\n            m_pTexture = NULL;\n            m_bCreatedTexture = false;\n        }\n    }\n\n    m_pTexture = (CGUITexture_Impl*)pTexture;\n\n    // Get CEGUI texture\n    CEGUI::Texture* pCEGUITexture = m_pTexture->GetTexture();\n\n    // Get an unique identifier for CEGUI for the imageset\n    char szUnique[CGUI_CHAR_SIZE];\n    m_pGUI->GetUniqueName(szUnique);\n\n    // Create an imageset\n    if (!m_pImageset)\n    {\n        while (m_pImagesetManager->isImagesetPresent(szUnique))\n            m_pGUI->GetUniqueName(szUnique);\n        m_pImageset = m_pImagesetManager->createImageset(szUnique, pCEGUITexture, true);\n    }\n\n    // Get an unique identifier for CEGUI for the image\n    m_pGUI->GetUniqueName(szUnique);\n\n    // Define an image and get its pointer\n    m_pImageset->defineImage(szUnique, CEGUI::Point(0, 0), CEGUI::Size(pCEGUITexture->getWidth(), pCEGUITexture->getHeight()), CEGUI::Point(0, 0));\n    m_pImage = &m_pImageset->getImage(szUnique);\n\n    // Set the image just loaded as the image to be drawn for the widget\n    reinterpret_cast<CEGUI::StaticImage*>(m_pWindow)->setImage(m_pImage);\n\n    // Success\n    return true;\n}\n''',
    '''bool CGUIStaticImage_Impl::LoadFromTexture(CGUITexture* pTexture)\n{\n    if (!pTexture)\n        return false;\n\n    if (m_pTexture && pTexture != m_pTexture)\n    {\n        // Detach all CEGUI references before an owned texture is replaced.\n        reinterpret_cast<CEGUI::StaticImage*>(m_pWindow)->setImage(nullptr);\n        if (m_pImageset)\n        {\n            m_pImagesetManager->destroyImageset(m_pImageset);\n            m_pImageset = nullptr;\n        }\n        m_pImage = nullptr;\n\n        if (m_bCreatedTexture)\n        {\n            delete m_pTexture;\n            m_pTexture = NULL;\n            m_bCreatedTexture = false;\n        }\n    }\n\n    m_pTexture = (CGUITexture_Impl*)pTexture;\n    CEGUI::Texture* pCEGUITexture = m_pTexture->GetTexture();\n    if (!pCEGUITexture)\n        return false;\n\n    char szUnique[CGUI_CHAR_SIZE];\n    m_pGUI->GetUniqueName(szUnique);\n\n    try\n    {\n        if (m_pImageset && m_pImage)\n            m_pImageset->undefineAllImages();\n\n        if (!m_pImageset)\n        {\n            while (m_pImagesetManager->isImagesetPresent(szUnique))\n                m_pGUI->GetUniqueName(szUnique);\n            m_pImageset = m_pImagesetManager->createImageset(szUnique, pCEGUITexture, true);\n        }\n\n        m_pGUI->GetUniqueName(szUnique);\n        m_pImageset->defineImage(szUnique, CEGUI::Point(0, 0), CEGUI::Size(pCEGUITexture->getWidth(), pCEGUITexture->getHeight()), CEGUI::Point(0, 0));\n        m_pImage = &m_pImageset->getImage(szUnique);\n        reinterpret_cast<CEGUI::StaticImage*>(m_pWindow)->setImage(m_pImage);\n    }\n    catch (const CEGUI::Exception& e)\n    {\n        OutputDebugLine(SString("CGUIStaticImage_Impl::LoadFromTexture failed: %s", e.getMessage().c_str()));\n        reinterpret_cast<CEGUI::StaticImage*>(m_pWindow)->setImage(nullptr);\n        m_pImage = nullptr;\n        return false;\n    }\n\n    return true;\n}\n''',
    "harden static-image texture replacement",
)


# Web browser texture lifetime: keep the dynamically allocated wrapper so failed
# and repeated LoadFromWebView calls can release it cleanly.
replace_exact(
    "Client/gui/CGUIWebBrowser_Impl.h",
    '''class CGUITexture_Impl;\nclass CGUI_Impl;\nclass CWebViewInterface;\n''',
    '''class CGUITexture_Impl;\nclass CGUI_Impl;\nclass CGUIWebBrowserTexture;\nclass CWebViewInterface;\n''',
    "forward declare browser texture wrapper",
)
replace_exact(
    "Client/gui/CGUIWebBrowser_Impl.h",
    '''    CEGUI::Imageset*        m_pImageset;\n    CEGUI::Image*           m_pImage;\n\n    CWebViewInterface* m_pWebView;\n''',
    '''    CEGUI::Imageset*        m_pImageset;\n    CEGUI::Image*           m_pImage;\n    CGUIWebBrowserTexture*  m_pTexture;\n\n    CWebViewInterface* m_pWebView;\n''',
    "track browser texture wrapper",
)
replace_exact(
    "Client/gui/CGUIWebBrowser_Impl.cpp",
    '''    m_pImagesetManager = pGUI->GetImageSetManager();\n    m_pImageset = nullptr;\n    m_pImage = nullptr;\n    m_pGUI = pGUI;\n''',
    '''    m_pImagesetManager = pGUI->GetImageSetManager();\n    m_pImageset = nullptr;\n    m_pImage = nullptr;\n    m_pTexture = nullptr;\n    m_pGUI = pGUI;\n''',
    "initialize browser texture wrapper",
)
replace_exact(
    "Client/gui/CGUIWebBrowser_Impl.cpp",
    '''    if (m_pImageset)\n    {\n        m_pImageset->undefineAllImages();\n        m_pImagesetManager->destroyImageset(m_pImageset);\n        m_pImage = nullptr;\n        m_pImageset = nullptr;\n    }\n}\n''',
    '''    if (m_pImageset)\n    {\n        m_pImageset->undefineAllImages();\n        m_pImagesetManager->destroyImageset(m_pImageset);\n        m_pImage = nullptr;\n        m_pImageset = nullptr;\n    }\n\n    if (m_pTexture)\n    {\n        delete m_pTexture;\n        m_pTexture = nullptr;\n    }\n}\n''',
    "release browser texture wrapper",
)
replace_exact(
    "Client/gui/CGUIWebBrowser_Impl.cpp",
    '''void CGUIWebBrowser_Impl::LoadFromWebView(CWebViewInterface* pWebView)\n{\n    m_pWebView = pWebView;\n\n    if (m_pImageset && m_pImage)\n    {\n        m_pImageset->undefineAllImages();\n    }\n\n    CGUIWebBrowserTexture* pCEGUITexture = new CGUIWebBrowserTexture(m_pGUI->GetRenderer(), m_pWebView);\n\n    // Get an unique identifier for CEGUI for the imageset\n    char szUnique[CGUI_CHAR_SIZE];\n    m_pGUI->GetUniqueName(szUnique);\n\n    // Create an imageset\n    if (!m_pImageset)\n    {\n        while (m_pImagesetManager->isImagesetPresent(szUnique))\n            m_pGUI->GetUniqueName(szUnique);\n        m_pImageset = m_pImagesetManager->createImageset(szUnique, pCEGUITexture, true);\n    }\n\n    // Get an unique identifier for CEGUI for the image\n    m_pGUI->GetUniqueName(szUnique);\n\n    // Define an image and get its pointer\n    m_pImageset->defineImage(szUnique, CEGUI::Point(0, 0), CEGUI::Size(pCEGUITexture->getWidth(), pCEGUITexture->getHeight()), CEGUI::Point(0, 0));\n    m_pImage = const_cast<CEGUI::Image*>(\n        &m_pImageset->getImage(szUnique));  // const_cast here is a huge hack, but is okay here since all images generated here are unique\n\n    // Set the image just loaded as the image to be drawn for the widget\n    reinterpret_cast<CEGUI::StaticImage*>(m_pWindow)->setImage(m_pImage);\n}\n''',
    '''void CGUIWebBrowser_Impl::LoadFromWebView(CWebViewInterface* pWebView)\n{\n    m_pWebView = pWebView;\n\n    if (!m_pWebView)\n    {\n        Clear();\n        return;\n    }\n\n    Clear();\n\n    try\n    {\n        m_pTexture = new CGUIWebBrowserTexture(m_pGUI->GetRenderer(), m_pWebView);\n\n        char szUnique[CGUI_CHAR_SIZE];\n        m_pGUI->GetUniqueName(szUnique);\n\n        while (m_pImagesetManager->isImagesetPresent(szUnique))\n            m_pGUI->GetUniqueName(szUnique);\n        m_pImageset = m_pImagesetManager->createImageset(szUnique, m_pTexture, true);\n\n        m_pGUI->GetUniqueName(szUnique);\n        m_pImageset->defineImage(szUnique, CEGUI::Point(0, 0), CEGUI::Size(m_pTexture->getWidth(), m_pTexture->getHeight()), CEGUI::Point(0, 0));\n        m_pImage = const_cast<CEGUI::Image*>(&m_pImageset->getImage(szUnique));\n        reinterpret_cast<CEGUI::StaticImage*>(m_pWindow)->setImage(m_pImage);\n    }\n    catch (const CEGUI::Exception& e)\n    {\n        OutputDebugLine(SString("CGUIWebBrowser_Impl::LoadFromWebView failed: %s", e.getMessage().c_str()));\n        Clear();\n    }\n}\n''',
    "harden browser web-view loading",
)

replace_exact(
    "Client/gui/CGUIWebBrowser_Impl.cpp",
    '''bool CGUIWebBrowser_Impl::HasInputFocus()\n{\n    return m_pWebView->HasInputFocus();\n}\n''',
    '''bool CGUIWebBrowser_Impl::HasInputFocus()\n{\n    if (!m_pWebView)\n        return false;\n    return m_pWebView->HasInputFocus();\n}\n''',
    "guard browser input-focus query",
)

for method in [
    "Event_MouseButtonDown",
    "Event_MouseButtonUp",
    "Event_MouseDoubleClick",
    "Event_MouseMove",
    "Event_MouseWheel",
    "Event_Activated",
    "Event_Deactivated",
]:
    replace_exact(
        "Client/gui/CGUIWebBrowser_Impl.cpp",
        f'''bool CGUIWebBrowser_Impl::{method}(const CEGUI::EventArgs& e)\n{{\n''',
        f'''bool CGUIWebBrowser_Impl::{method}(const CEGUI::EventArgs& e)\n{{\n    if (!m_pWebView)\n        return true;\n\n''',
        f"guard browser callback {method}",
    )

print("Somnis safe client backports pass 6:")
for label, path in changed:
    print(f"  APPLIED: {label} [{path}]")
for label, path in already:
    print(f"  ALREADY: {label} [{path}]")
for label, path, reason in skipped:
    print(f"  SKIPPED: {label} [{path}] - {reason}")
print(f"summary: applied={len(changed)} already={len(already)} skipped={len(skipped)}")
