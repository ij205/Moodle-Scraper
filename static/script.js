document.addEventListener("DOMContentLoaded", () => {
    const startBtn = document.getElementById("start-btn");
    const startLlmBtn = document.getElementById("start-llm-btn");
    const saveConfigBtn = document.getElementById("save-config-btn");
    const clearConfigBtn = document.getElementById("clear-config-btn");
    const statusText = document.getElementById("status-text");
    const statusCard = document.getElementById("status-card");
    const statusIcon = document.getElementById("status-icon");

    const configSection = document.getElementById("config-section");
    const configList = document.getElementById("config-list");

    // Auth Elements
    const userInfo = document.getElementById("user-info");
    const authBtn = document.getElementById("auth-btn");
    const logoutBtn = document.getElementById("logout-btn");
    const deleteAccountBtn = document.getElementById("delete-account-btn");

    const authModal = document.getElementById("auth-modal");
    const modalClose = document.getElementById("modal-close");
    const tabLoginBtn = document.getElementById("tab-login-btn");
    const tabRegisterBtn = document.getElementById("tab-register-btn");

    const loginForm = document.getElementById("login-form");
    const registerForm = document.getElementById("register-form");
    const authError = document.getElementById("auth-error");

    let discoveredCourses = [];
    let scheduleMap = {};
    let hasSavedConfig = false;

    if (typeof marked !== "undefined") {
        const renderer = new marked.Renderer();
        const originalLinkRenderer = renderer.link.bind(renderer);

        renderer.link = (href, title, text) => {
            const html = originalLinkRenderer(href, title, text);
            return html.replace(/^<a /, '<a target="_blank" rel="noopener noreferrer" ');
        };

        marked.setOptions({ renderer });
    }

    function cleanCourseTitle(title) {
        if (!title) return "";
        
        let cleaned = title.replace(/^course\s*name\s*/i, "").trim();

        const mid = Math.floor(cleaned.length / 2);
        if (cleaned.length > 0) {
            const firstHalf = cleaned.slice(0, mid).trim();
            const secondHalf = cleaned.slice(mid).trim();
            if (firstHalf === secondHalf) {
                cleaned = firstHalf;
            }
        }

        const parts = cleaned.split(/\s+[-–]\s+/);
        const uniqueParts = [];
        parts.forEach((part) => {
            const trimmed = part.trim();
            if (trimmed && !uniqueParts.some(p => p.toLowerCase() === trimmed.toLowerCase())) {
                uniqueParts.push(trimmed);
            }
        });

        return uniqueParts.join(" - ");
    }

    // --- Authentication & Configuration Handlers ---

    async function checkAuthSession() {
        try {
            const res = await fetch("/api/me");
            const data = await res.json();
            if (data.authenticated) {
                setLoggedInUI(data.email);
                await checkSavedConfig();
            } else {
                setLoggedOutUI();
            }
        } catch (err) {
            console.error("Auth check failed:", err);
            setLoggedOutUI();
        }
    }

    async function checkSavedConfig() {
        try {
            const res = await fetch("/api/config");
            const data = await res.json();
            if (data.has_config) {
                hasSavedConfig = true;
                clearConfigBtn.classList.remove("hidden");
            } else {
                hasSavedConfig = false;
                clearConfigBtn.classList.add("hidden");
            }
        } catch (err) {
            console.error("Failed to check saved configuration:", err);
        }
    }

    function setLoggedInUI(email) {
        userInfo.textContent = `Logged in as: ${email}`;
        authBtn.classList.add("hidden");
        logoutBtn.classList.remove("hidden");
        deleteAccountBtn.classList.remove("hidden");
        startBtn.disabled = false;
    }

    function setLoggedOutUI() {
        userInfo.textContent = "Not logged in";
        authBtn.classList.remove("hidden");
        logoutBtn.classList.add("hidden");
        deleteAccountBtn.classList.add("hidden");
        clearConfigBtn.classList.add("hidden");
        startBtn.disabled = true;
    }

    authBtn.addEventListener("click", () => {
        authModal.classList.remove("hidden");
        showError("");
    });

    modalClose.addEventListener("click", () => {
        authModal.classList.add("hidden");
    });

    tabLoginBtn.addEventListener("click", () => {
        tabLoginBtn.classList.add("active");
        tabRegisterBtn.classList.remove("active");
        loginForm.classList.remove("hidden");
        registerForm.classList.add("hidden");
        showError("");
    });

    tabRegisterBtn.addEventListener("click", () => {
        tabRegisterBtn.classList.add("active");
        tabLoginBtn.classList.remove("active");
        registerForm.classList.remove("hidden");
        loginForm.classList.add("hidden");
        showError("");
    });

    function showError(msg) {
        if (!msg) {
            authError.classList.add("hidden");
            authError.textContent = "";
        } else {
            authError.textContent = msg;
            authError.classList.remove("hidden");
        }
    }

    loginForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        showError("");

        const email = document.getElementById("login-email").value;
        const password = document.getElementById("login-password").value;

        try {
            const res = await fetch("/api/login", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ email, password })
            });

            const data = await res.json();
            if (res.ok && data.success) {
                setLoggedInUI(data.email);
                authModal.classList.add("hidden");
                loginForm.reset();
                await checkSavedConfig();
            } else {
                showError(data.error || "Login failed.");
            }
        } catch (err) {
            showError("A network error occurred. Please try again.");
        }
    });

    registerForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        showError("");

        const email = document.getElementById("register-email").value;
        const password = document.getElementById("register-password").value;
        const appPassword = document.getElementById("register-app-password").value;

        try {
            const res = await fetch("/api/register", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ email, password, app_password: appPassword })
            });

            const data = await res.json();
            if (res.ok && data.success) {
                setLoggedInUI(data.email);
                authModal.classList.add("hidden");
                registerForm.reset();
                await checkSavedConfig();
            } else {
                showError(data.error || "Registration failed.");
            }
        } catch (err) {
            showError("A network error occurred. Please try again.");
        }
    });

    logoutBtn.addEventListener("click", async () => {
        await fetch("/api/logout", { method: "POST" });
        setLoggedOutUI();
        configSection.classList.add("hidden");
        statusCard.classList.add("hidden");
        document.getElementById("tabs-header").innerHTML = "";
        document.getElementById("tabs-content").innerHTML = "";
    });

    deleteAccountBtn.addEventListener("click", async () => {
        if (confirm("Are you sure you want to delete your account? This action cannot be undone.")) {
            const res = await fetch("/api/delete-account", { method: "POST" });
            if (res.ok) {
                setLoggedOutUI();
                configSection.classList.add("hidden");
                statusCard.classList.add("hidden");
                document.getElementById("tabs-header").innerHTML = "";
                document.getElementById("tabs-content").innerHTML = "";
            }
        }
    });

    clearConfigBtn.addEventListener("click", async () => {
        if (confirm("Are you sure you want to clear your saved course configuration?")) {
            const res = await fetch("/api/config", { method: "DELETE" });
            if (res.ok) {
                hasSavedConfig = false;
                clearConfigBtn.classList.add("hidden");
                alert("Configuration cleared. Scraping will now run fresh course discovery.");
            }
        }
    });

    checkAuthSession();

    // --- Formatting Utilities ---

    function parseMarkdown(content) {
        if (!content || content === "N/A") return "<p>N/A</p>";
    
        // Replace custom YouTube markers with responsive HTML iframes
        const youtubePattern = /\[YOUTUBE_EMBED:(https?:\/\/[^\]]+)\]/g;
        let htmlContent = content.replace(youtubePattern, (match, embedUrl) => {
            return `
                <div class="video-container" style="position: relative; width: 100%; padding-bottom: 56.25%; height: 0; overflow: hidden; border-radius: 8px; margin: 15px 0;">
                    <iframe src="${embedUrl}" title="YouTube video player" frameborder="0" 
                        allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" 
                        allowfullscreen style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; border: 0;">
                    </iframe>
                </div>
            `;
        });

        return typeof marked !== "undefined" ? marked.parse(htmlContent) : escapeHtml(htmlContent);
    }

    function escapeHtml(text) {
        return text
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    startBtn.addEventListener("click", async () => {
        if (hasSavedConfig) {
            runScrapeDirectly();
        } else {
            runCourseDiscovery();
        }
    });

    function runCourseDiscovery() {
        startBtn.disabled = true;
        statusCard.classList.remove("hidden");
        statusIcon.className = "spinner";
        statusText.textContent = "Connecting to server...";
        configList.innerHTML = "";

        const tabsHeader = document.getElementById("tabs-header");
        const tabsContent = document.getElementById("tabs-content");
        if (tabsHeader) tabsHeader.innerHTML = "";
        if (tabsContent) tabsContent.innerHTML = "";

        configSection.classList.add("hidden");

        const eventSource = new EventSource("/stream-discover");

        eventSource.addEventListener("status", (e) => {
            statusText.textContent = JSON.parse(e.data);
        });

        eventSource.addEventListener("discovery_complete", (e) => {
            discoveredCourses = JSON.parse(e.data);
            eventSource.close();
            startBtn.disabled = false;

            statusIcon.className = "checkmark";
            renderCourseList(discoveredCourses);
        });

        eventSource.onerror = (err) => {
            console.error("SSE Error:", err);
            statusText.textContent = "Error occurred during course discovery.";
            statusIcon.className = "spinner";
            eventSource.close();
            startBtn.disabled = false;
        };
    }

    function renderCourseList(courses) {
        if (!courses || courses.length === 0) {
            statusText.textContent = "No courses discovered.";
            return;
        }

        configList.innerHTML = "";
        scheduleMap = {};

        courses.forEach((c) => {
            scheduleMap[c.id] = "A";
            const title = cleanCourseTitle(c.title);

            const row = document.createElement("div");
            row.className = "config-item";
            row.innerHTML = `
                <span class="config-title">${escapeHtml(title)}</span>
                <div class="switch-container">
                    <span class="day-label" id="label_${c.id}">A-Day</span>
                    <label class="switch">
                        <input type="checkbox" id="switch_${c.id}">
                        <span class="slider round"></span>
                    </label>
                </div>
            `;

            configList.appendChild(row);

            const checkbox = row.querySelector(`#switch_${c.id}`);
            const label = row.querySelector(`#label_${c.id}`);

            checkbox.addEventListener("change", (e) => {
                if (e.target.checked) {
                    scheduleMap[c.id] = "B";
                    label.textContent = "B-Day";
                } else {
                    scheduleMap[c.id] = "A";
                    label.textContent = "A-Day";
                }
            });
        });

        configSection.classList.remove("hidden");
    }

    saveConfigBtn.addEventListener("click", async () => {
        saveConfigBtn.disabled = true;

        const configPayload = discoveredCourses.map((c) => ({
            title: c.title,
            category: c.category,
            url: c.url,
            schedule_day: scheduleMap[c.id] || "A"
        }));

        try {
            const res = await fetch("/api/config", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ courses: configPayload })
            });

            const data = await res.json();
            if (res.ok && data.success) {
                hasSavedConfig = true;
                clearConfigBtn.classList.remove("hidden");
                alert("Configuration saved successfully!");
            } else {
                alert("Failed to save configuration.");
            }
        } catch (err) {
            alert("Error saving configuration.");
        } finally {
            saveConfigBtn.disabled = false;
        }
    });

    startLlmBtn.addEventListener("click", () => {
        runScrapeDirectly();
    });

    function runScrapeDirectly() {
        startBtn.disabled = true;
        startLlmBtn.disabled = true;
        statusCard.classList.remove("hidden");
        
        statusIcon.className = "spinner";
        statusText.textContent = "Initializing concurrent extraction...";

        const tabsHeader = document.getElementById("tabs-header");
        const tabsContent = document.getElementById("tabs-content");
        if (tabsHeader) tabsHeader.innerHTML = "";
        if (tabsContent) tabsContent.innerHTML = "";

        const queryParam = encodeURIComponent(JSON.stringify(scheduleMap));
        const eventSource = new EventSource(`/stream-scrape?schedules=${queryParam}`);

        let totalCourses = 0;
        let completedCourses = 0;

        eventSource.addEventListener("status", (e) => {
            const data = JSON.parse(e.data);
            if (typeof data === "object" && data.total) {
                totalCourses = data.total;
                statusText.textContent = `Scraped 0 of ${totalCourses} courses...`;
            } else {
                statusText.textContent = data;
            }
        });

        eventSource.addEventListener("course_complete", (e) => {
            completedCourses++;
            if (totalCourses > 0) {
                statusText.textContent = `Scraped ${completedCourses} of ${totalCourses} courses...`;
            } else {
                statusText.textContent = `Scraped ${completedCourses} courses...`;
            }
        });

        eventSource.addEventListener("scrape_complete", (e) => {
            const cache = JSON.parse(e.data);
            eventSource.close();
            startBtn.disabled = false;
            startLlmBtn.disabled = false;

            renderResults(cache);
        });

        eventSource.onerror = (err) => {
            console.error("SSE Error:", err);
            statusText.textContent = "Error occurred during page extraction.";
            eventSource.close();
            startBtn.disabled = false;
            startLlmBtn.disabled = false;
        };
    }

    function renderResults(cache) {
        statusCard.classList.add("hidden");
        configSection.classList.add("hidden");

        const tabsHeader = document.getElementById("tabs-header");
        const tabsContent = document.getElementById("tabs-content");

        tabsHeader.innerHTML = "";
        tabsContent.innerHTML = "";

        if (!cache || cache.length === 0) {
            tabsContent.innerHTML = "<p>No data extracted.</p>";
            return;
        }

        tabsHeader.classList.remove("hidden");

        cache.forEach((res, index) => {
            const title = cleanCourseTitle(res.title);
            const isSuccess = res.status === "Success";

            const tabBtn = document.createElement("button");
            tabBtn.className = `tab-btn ${index === 0 ? "active" : ""}`;
            
            const dotClass = isSuccess ? "green" : "red";
            tabBtn.innerHTML = `
                <span class="tab-dot ${dotClass}"></span>
                <span>${escapeHtml(title)}</span>
            `;

            tabBtn.addEventListener("click", () => {
                document.querySelectorAll(".tab-btn").forEach(btn => btn.classList.remove("active"));
                document.querySelectorAll(".course-card-tab").forEach(card => card.classList.add("hidden"));

                tabBtn.classList.add("active");
                document.getElementById(`course-tab-${index}`).classList.remove("hidden");
            });

            tabsHeader.appendChild(tabBtn);

            const card = document.createElement("div");
            card.id = `course-tab-${index}`;
            card.className = `course-card course-card-tab ${index !== 0 ? "hidden" : ""}`;

            if (!isSuccess) {
                card.innerHTML = `
                    <div class="course-header">
                        <h2>${escapeHtml(title)}</h2>
                        <span class="badge poor">Poor Maintenance</span>
                    </div>
                    <p style="color: #666; font-size: 14px;">No active lesson entries found matching current schedule date criteria.</p>
                `;
            } else {
                const ext = res.extracted || {};
                card.innerHTML = `
                    <div class="course-header">
                        <h2>${escapeHtml(title)}</h2>
                        <span class="badge success">Success</span>
                    </div>
                    <div class="extraction-section">
                        <h3>Objectives</h3>
                        <div class="markdown-content">${parseMarkdown(ext.objectives)}</div>
                    </div>
                    <div class="extraction-section">
                        <h3>Classroom Activities</h3>
                        <div class="markdown-content">${parseMarkdown(ext.classroom_activities)}</div>
                    </div>
                    <div class="extraction-section">
                        <h3>Homework Due</h3>
                        <div class="markdown-content">${parseMarkdown(ext.homework_due)}</div>
                    </div>
                    <div class="extraction-section">
                        <h3>Homework Assigned</h3>
                        <div class="markdown-content">${parseMarkdown(ext.homework_assigned)}</div>
                    </div>
                `;
            }

            tabsContent.appendChild(card);
        });
    }
});