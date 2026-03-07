document.addEventListener("click", function (event) {
    const openBtn = event.target.closest("[data-open-modal]");
    if (openBtn) {
        const modalId = openBtn.getAttribute("data-open-modal");
        const modal = document.getElementById(modalId);
        if (modal) {
            modal.classList.add("is-open");
            document.body.style.overflow = "hidden";
        }
        return;
    }

    const closeBtn = event.target.closest("[data-close-modal]");
    if (closeBtn) {
        const modal = closeBtn.closest(".modal");
        if (modal) {
            modal.classList.remove("is-open");
            document.body.style.overflow = "";
        }
    }
});

document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
        document.querySelectorAll(".modal.is-open").forEach(function (modal) {
            modal.classList.remove("is-open");
        });
        document.body.style.overflow = "";
    }
});

const loginButtons = document.querySelectorAll(".login-page .auth-btn");
const secureLoader = document.getElementById("secureLoader");

loginButtons.forEach(function (button) {
    button.addEventListener("click", function (event) {
        // Keep normal browser behavior for new-tab/window actions.
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) {
            return;
        }

        const targetUrl = button.getAttribute("href");
        if (!targetUrl || !secureLoader) {
            return;
        }

        event.preventDefault();
        secureLoader.classList.add("is-visible");
        document.body.style.overflow = "hidden";

        const delay = 1200 + Math.floor(Math.random() * 601); // 1.2s to 1.8s
        window.setTimeout(function () {
            window.location.href = targetUrl;
        }, delay);
    });
});

// Student submission mode toggle (File vs Link)
const submissionType = document.getElementById("submission_type");
const submissionUrlWrap = document.getElementById("submission_url_wrap");
const submissionUrlInput = document.getElementById("submission_url");
const projectFileInput = document.getElementById("project_file");

function updateSubmissionModeUI() {
    if (!submissionType || !submissionUrlWrap || !submissionUrlInput || !projectFileInput) {
        return;
    }
    const mode = submissionType.value;
    if (mode === "File") {
        submissionUrlWrap.style.display = "none";
        submissionUrlInput.required = false;
        submissionUrlInput.value = "";
        projectFileInput.required = true;
    } else {
        submissionUrlWrap.style.display = "grid";
        submissionUrlInput.required = true;
        projectFileInput.required = false;
    }
}

if (submissionType) {
    updateSubmissionModeUI();
    submissionType.addEventListener("change", updateSubmissionModeUI);
}


