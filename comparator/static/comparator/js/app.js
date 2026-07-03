/* ==========================================================
   Workspace Comparator -- Front-end logic
   Two-panel table rendering  +  Folder browser modal
   ========================================================== */
(function () {
  "use strict";

  // ---- DOM refs ----
  var leftInput      = document.getElementById("leftDir");
  var rightInput     = document.getElementById("rightDir");
  var btnCompare     = document.getElementById("btnCompare");
  var btnClear       = document.getElementById("btnClear");
  var loadingOvl     = document.getElementById("loadingOverlay");
  var statsBar       = document.getElementById("statsBar");
  var resultsSection = document.getElementById("resultsSection");
  var emptyState     = document.getElementById("emptyState");
  var compBody       = document.getElementById("comparisonBody");

  // Browse modal refs
  var browseModal     = document.getElementById("browseModal");
  var modalBody       = document.getElementById("modalBody");
  var modalCurPath    = document.getElementById("modalCurrentPath");
  var btnModalUp      = document.getElementById("btnModalUp");
  var btnModalSelect  = document.getElementById("btnModalSelect");
  var btnModalCancel  = document.getElementById("btnModalCancel");
  var btnModalClose   = document.getElementById("btnModalClose");

  // Which input the browse modal is targeting
  var browseTarget = null;
  var browseCurrentPath = "";
  var browseParentPath = "";

  // ==================================================================
  // Compare button enable/disable
  // ==================================================================
  function updateButton() {
    btnCompare.disabled = !(leftInput.value.trim() && rightInput.value.trim());
  }
  leftInput.addEventListener("input", updateButton);
  rightInput.addEventListener("input", updateButton);

  // ==================================================================
  // Drag & drop  (best-effort path extraction)
  // ==================================================================
  function setupDrop(zone, input) {
    zone.addEventListener("dragover", function (e) {
      e.preventDefault();
      zone.classList.add("drag-over");
    });
    zone.addEventListener("dragleave", function () {
      zone.classList.remove("drag-over");
    });
    zone.addEventListener("drop", function (e) {
      e.preventDefault();
      zone.classList.remove("drag-over");

      // 1. Try text/plain (some apps put full path here)
      var text = (e.dataTransfer.getData("text/plain") ||
                  e.dataTransfer.getData("Text") || "").trim();

      // 2. If we got something that looks like a full path, use it
      if (text && looksLikeFullPath(text)) {
        input.value = text;
        updateButton();
        return;
      }

      // 3. Browsers only give the folder NAME, not the full path.
      //    Show a helpful message.
      var folderName = "";
      if (e.dataTransfer.items && e.dataTransfer.items.length) {
        var item = e.dataTransfer.items[0];
        if (item.kind === "file") {
          var entry = item.webkitGetAsEntry && item.webkitGetAsEntry();
          if (entry) {
            folderName = entry.name;
          } else {
            var file = item.getAsFile();
            if (file) folderName = file.name;
          }
        }
      }
      if (!folderName && e.dataTransfer.files && e.dataTransfer.files.length) {
        folderName = e.dataTransfer.files[0].name;
      }

      if (folderName) {
        showError(
          'Dropped "' + folderName + '". Browsers cannot read the full path. ' +
          'Please use the Browse button or type the complete path.'
        );
      }
    });
  }

  function looksLikeFullPath(s) {
    // Windows full path:  C:\... or D:\...
    // Unix full path:     /home/...
    return /^[A-Za-z]:[\\\/]/.test(s) || s.charAt(0) === "/";
  }

  setupDrop(document.getElementById("dropLeft"), leftInput);
  setupDrop(document.getElementById("dropRight"), rightInput);

  // ==================================================================
  // Clear
  // ==================================================================
  btnClear.addEventListener("click", function () {
    leftInput.value = "";
    rightInput.value = "";
    updateButton();
    statsBar.classList.add("hidden");
    resultsSection.classList.add("hidden");
    emptyState.classList.remove("hidden");
    compBody.innerHTML = "";
  });

  // ==================================================================
  // Compare
  // ==================================================================
  btnCompare.addEventListener("click", runComparison);
  leftInput.addEventListener("keydown", function (e) { if (e.key === "Enter") runComparison(); });
  rightInput.addEventListener("keydown", function (e) { if (e.key === "Enter") runComparison(); });

  function runComparison() {
    var left = leftInput.value.trim();
    var right = rightInput.value.trim();
    if (!left || !right) return;

    showLoading(true);
    compBody.innerHTML = "";
    emptyState.classList.add("hidden");
    statsBar.classList.add("hidden");
    resultsSection.classList.add("hidden");

    fetch("/api/compare/", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ left_dir: left, right_dir: right }),
    })
      .then(function (res) {
        if (!res.ok) {
          return res.json().then(function (d) {
            throw new Error(d.error || "Server error " + res.status);
          });
        }
        return res.json();
      })
      .then(function (data) {
        showLoading(false);
        renderResults(data);
      })
      .catch(function (err) {
        showLoading(false);
        showError(err.message || "Comparison failed");
      });
  }

  // ==================================================================
  // Loading
  // ==================================================================
  function showLoading(on) {
    if (on) {
      loadingOvl.classList.remove("hidden");
      btnCompare.disabled = true;
    } else {
      loadingOvl.classList.add("hidden");
      updateButton();
    }
  }

  // ==================================================================
  // Error toast
  // ==================================================================
  function showError(msg) {
    var el = document.createElement("div");
    el.className = "error-toast";
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(function () { el.remove(); }, 6000);
  }

  // ==================================================================
  // HTML escape
  // ==================================================================
  function esc(str) {
    var d = document.createElement("div");
    d.appendChild(document.createTextNode(str));
    return d.innerHTML;
  }

  // ==================================================================
  // Render results into the two-panel table
  // ==================================================================
  function renderResults(data) {
    var matched = data.matched || [];
    var unmL    = data.unmatched_left || [];
    var unmR    = data.unmatched_right || [];
    var stats   = data.stats || {};

    // Stats bar
    document.getElementById("statTotal").innerHTML =
      'Files: <span class="badge badge-blue">' + stats.total_left + " left</span> / " +
      '<span class="badge badge-blue">' + stats.total_right + " right</span>";
    document.getElementById("statExact").innerHTML =
      'Exact path: <span class="badge badge-green">' + stats.exact_path_matches + "</span>";
    document.getElementById("statDet").innerHTML =
      'Deterministic: <span class="badge badge-orange">' + stats.deterministic_matches + "</span>";
    document.getElementById("statLLM").innerHTML =
      'LLM verified: <span class="badge badge-purple">' + stats.llm_matches + "</span>";
    document.getElementById("statCalls").innerHTML =
      'LLM calls: <span class="badge badge-gray">' + stats.llm_calls + "</span>";
    statsBar.classList.remove("hidden");

    // Build table body
    var fragment = document.createDocumentFragment();

    // SECTION 1: Matched files (green)
    if (matched.length > 0) {
      fragment.appendChild(createSeparatorRow(
        "Corresponding Files (" + matched.length + ")"
      ));

      matched.forEach(function (m) {
        var tr = document.createElement("tr");
        tr.className = "row-matched";

        var simClass = "sim-high";
        if (m.similarity < 70) simClass = "sim-low";
        else if (m.similarity < 90) simClass = "sim-medium";

        var methodLabel = m.match_type.replace(/_/g, " ");

        tr.innerHTML =
          '<td class="cell-left">' + esc(m.left.name) + "</td>" +
          '<td class="cell-left">' + esc(m.left.directory || ".") + "</td>" +
          '<td class="match-cell">' +
            '<span class="sim-badge ' + simClass + '">' + Math.round(m.similarity) + "%</span>" +
            '<span class="match-method">' + esc(methodLabel) + "</span>" +
          "</td>" +
          '<td class="cell-right">' + esc(m.right.name) + "</td>" +
          '<td class="cell-right">' + esc(m.right.directory || ".") + "</td>";

        fragment.appendChild(tr);
      });
    }

    // SECTION 2: Unmatched files (red)
    var hasUnmatched = unmL.length > 0 || unmR.length > 0;
    if (hasUnmatched) {
      fragment.appendChild(createSeparatorRow(
        "Unmatched Files (" + (unmL.length + unmR.length) + ")"
      ));

      // Unmatched left: red on left side, empty on right
      unmL.forEach(function (f) {
        var tr = document.createElement("tr");
        tr.className = "row-unmatched";
        tr.innerHTML =
          '<td class="cell-left-filled">' + esc(f.name) + "</td>" +
          '<td class="cell-left-filled">' + esc(f.directory || ".") + "</td>" +
          '<td class="match-cell"><span class="no-match-indicator">--</span></td>' +
          '<td class="cell-empty"></td>' +
          '<td class="cell-empty"></td>';
        fragment.appendChild(tr);
      });

      // Unmatched right: empty on left, red on right
      unmR.forEach(function (f) {
        var tr = document.createElement("tr");
        tr.className = "row-unmatched";
        tr.innerHTML =
          '<td class="cell-empty"></td>' +
          '<td class="cell-empty"></td>' +
          '<td class="match-cell"><span class="no-match-indicator">--</span></td>' +
          '<td class="cell-right-filled">' + esc(f.name) + "</td>" +
          '<td class="cell-right-filled">' + esc(f.directory || ".") + "</td>';
        fragment.appendChild(tr);
      });
    }

    // Edge case: no files at all
    if (matched.length === 0 && !hasUnmatched) {
      var tr = document.createElement("tr");
      tr.className = "no-results-row";
      tr.innerHTML = '<td colspan="5">No source files found in the provided directories.</td>';
      fragment.appendChild(tr);
    }

    compBody.appendChild(fragment);
    resultsSection.classList.remove("hidden");
    resultsSection.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function createSeparatorRow(label) {
    var tr = document.createElement("tr");
    tr.className = "row-separator";
    tr.innerHTML =
      '<td colspan="2"><span class="sep-label">' + esc(label) + "</span></td>" +
      '<td class="match-cell"></td>' +
      '<td colspan="2"><span class="sep-label">' + esc(label) + "</span></td>";
    return tr;
  }

  // ==================================================================
  // FOLDER BROWSER MODAL
  // ==================================================================
  document.getElementById("btnBrowseLeft").addEventListener("click", function () {
    openBrowse(leftInput);
  });
  document.getElementById("btnBrowseRight").addEventListener("click", function () {
    openBrowse(rightInput);
  });

  btnModalCancel.addEventListener("click", closeBrowse);
  btnModalClose.addEventListener("click", closeBrowse);

  btnModalSelect.addEventListener("click", function () {
    if (browseTarget && browseCurrentPath) {
      browseTarget.value = browseCurrentPath;
      updateButton();
    }
    closeBrowse();
  });

  btnModalUp.addEventListener("click", function () {
    if (browseParentPath) {
      loadBrowse(browseParentPath);
    }
  });

  // Close on backdrop click
  browseModal.addEventListener("click", function (e) {
    if (e.target === browseModal) closeBrowse();
  });

  // Close on Escape
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !browseModal.classList.contains("hidden")) {
      closeBrowse();
    }
  });

  function openBrowse(targetInput) {
    browseTarget = targetInput;
    browseModal.classList.remove("hidden");

    // Start from current input value if it looks like a path, else from root
    var startPath = targetInput.value.trim();
    if (startPath && looksLikeFullPath(startPath)) {
      loadBrowse(startPath);
    } else {
      loadBrowse("");
    }
  }

  function closeBrowse() {
    browseModal.classList.add("hidden");
    browseTarget = null;
  }

  function loadBrowse(path) {
    modalBody.innerHTML = '<div class="folder-loading">Loading...</div>';
    modalCurPath.textContent = path || "(drives)";

    var url = "/api/browse/";
    if (path) {
      url += "?path=" + encodeURIComponent(path);
    }

    fetch(url)
      .then(function (res) {
        if (!res.ok) {
          return res.json().then(function (d) {
            throw new Error(d.error || "Error " + res.status);
          });
        }
        return res.json();
      })
      .then(function (data) {
        browseCurrentPath = data.current || "";
        browseParentPath = data.parent || "";
        modalCurPath.textContent = browseCurrentPath || "(drives)";

        if (!data.entries || data.entries.length === 0) {
          modalBody.innerHTML = '<div class="folder-empty">No subdirectories here</div>';
          return;
        }

        var ul = document.createElement("ul");
        ul.className = "folder-list";

        data.entries.forEach(function (entry) {
          var li = document.createElement("li");
          li.className = "folder-item";
          li.innerHTML =
            '<span class="folder-icon">&#128193;</span>' +
            '<span class="folder-name">' + esc(entry.name) + "</span>";
          li.addEventListener("click", function () {
            loadBrowse(entry.path);
          });
          ul.appendChild(li);
        });

        modalBody.innerHTML = "";
        modalBody.appendChild(ul);
      })
      .catch(function (err) {
        modalBody.innerHTML =
          '<div class="folder-empty" style="color:#c0392b">' +
          esc(err.message) + "</div>";
      });
  }

})();
