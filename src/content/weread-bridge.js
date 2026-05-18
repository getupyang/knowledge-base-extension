(() => {
  if (window.__kbWereadCopyBridgeInstalled) return;
  window.__kbWereadCopyBridgeInstalled = true;

  function selectedTextFallback() {
    try {
      const active = document.activeElement;
      if (active && "value" in active) return active.value || active.getAttribute("value") || "";
      return (window.getSelection && window.getSelection().toString()) || "";
    } catch {
      return "";
    }
  }

  function capture(actionId) {
    const originalExec = document.execCommand;
    const originalWriteText = navigator.clipboard && navigator.clipboard.writeText;
    let restored = false;
    let posted = false;

    function post(text) {
      if (posted) return;
      posted = true;
      window.postMessage({ __kb_weread_copy_capture: actionId, text: String(text || "") }, "*");
    }

    function restore() {
      if (restored) return;
      restored = true;
      try {
        if (document.execCommand === wrappedExec) document.execCommand = originalExec;
      } catch {}
      try {
        if (navigator.clipboard && navigator.clipboard.writeText === wrappedWriteText) {
          navigator.clipboard.writeText = originalWriteText;
        }
      } catch {}
    }

    function wrappedExec(command, ...args) {
      if (String(command || "").toLowerCase() === "copy") {
        const text = selectedTextFallback();
        setTimeout(() => {
          post(text);
          restore();
        }, 0);
      }
      return originalExec.apply(this, [command, ...args]);
    }

    async function wrappedWriteText(text, ...args) {
      post(text);
      restore();
      return originalWriteText.apply(this, [text, ...args]);
    }

    try {
      document.execCommand = wrappedExec;
    } catch {}
    try {
      if (navigator.clipboard && originalWriteText) navigator.clipboard.writeText = wrappedWriteText;
    } catch {}

    setTimeout(() => {
      try {
        const copyButton = document.querySelector(".reader_toolbar_container .toolbarItem.wr_copy");
        if (!copyButton) {
          post("");
          restore();
          return;
        }
        copyButton.click();
      } catch {
        post("");
        restore();
      }
    }, 0);

    setTimeout(() => {
      post("");
      restore();
    }, 1000);
  }

  window.addEventListener("message", (event) => {
    if (event.source !== window) return;
    const data = event.data || {};
    if (!data.__kb_weread_copy_capture_request) return;
    capture(String(data.__kb_weread_copy_capture_request));
  });
})();
