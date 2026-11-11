"""Detached async views cannot replace the active window's sidebar."""

import pytest

from tests.e2e.test_docs_ui import _login_and_open_docs

pytestmark = [pytest.mark.e2e, pytest.mark.ui]


# AC-FR0316-01@v0.10 TRACKS-TRACE late window loads preserve active sidebar
@pytest.mark.parametrize("view", ["releases", "docs", "settings"])
def test_detached_loader_cannot_replace_active_sidebar(ui_serve, page, view):
    ui_serve.seed_version_docs("v1.0")
    _login_and_open_docs(ui_serve, page)
    page.get_by_test_id("doc-tree-item-v1.0-spec").wait_for()
    page.get_by_test_id("tabbar-item-account").click()
    page.get_by_test_id("account-menu").wait_for()
    # The old main-body has been detached by a window switch. Let its real
    # loader finish against the live API, including the no-project path.
    page.evaluate("""async (view) => {
      const body = document.createElement('section');
      const module = await import(`/static/app/${view}.js`);
      const render = module[{
        docs: 'renderDocsView', releases: 'renderReleasesView', settings: 'renderSettingsView'
      }[view]];
      await render(body, {});
    }""", view)
    assert page.get_by_test_id("tabbar-item-account").get_attribute("aria-current") == "page"
    assert page.get_by_test_id("sidebar-heading").inner_text() == "account"
