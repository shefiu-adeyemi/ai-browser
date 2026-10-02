"""
The `>>>` selector: how a selector steps INTO a shadow root or a same-origin iframe.

A CSS selector can't cross those boundaries, so a selector may be written in pieces joined by `>>>`:
    #host >>> li.item          the li.item elements inside the shadow root of #host
    #fr >>> div.row            the div.row elements inside the page of the iframe #fr
    #app >>> #card >>> .price  and so on, as deep as the page goes
Each piece is an ordinary selector, searched inside the scope the pieces before it opened. An element with an
open shadow root opens that; an iframe opens its page if it comes from the same site (the page cannot see into
another site's frame, so nothing is found there). Pieces without `>>>` behave exactly as before.

The reader (playbook_scrape), the click-to-pick tool (playbook_picker) and the outline for the AI
(playbook_suggest) all use these helpers, so a selector that one of them makes is one the others can read.
"""

SEPARATOR = ">>>"

# JavaScript, pasted inside the arrow functions of those scripts (it only defines functions; it reads nothing itself).
HELPERS = r"""
  const inner = (el) => { try { return el.shadowRoot || el.contentDocument || null; } catch (e) { return null; } };
  const qsaAll = (root, sel) => {
    const parts = String(sel).split('>>>').map((s) => s.trim());
    let cur = [root];
    for (let i = 0; i < parts.length; i++) {
      const next = [];
      for (const r of cur) {
        const scope = i === 0 ? r : inner(r);
        if (!scope || !parts[i]) continue;
        for (const f of Array.from(scope.querySelectorAll(parts[i]))) next.push(f);
      }
      cur = next;
    }
    return cur;
  };
  const qs1 = (root, sel) => (String(sel).indexOf('>>>') < 0 ? root.querySelector(sel) : (qsaAll(root, sel)[0] || null));
  const hostOf = (root) => { try { return root.host || (root.defaultView && root.defaultView.frameElement) || null; } catch (e) { return null; } };
  const up = (el) => el.parentElement || hostOf(el.getRootNode());
"""
