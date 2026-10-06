
    let token = null;
    let convId = null;
    let currentActionContext = null; // Store context for PIN execution
    let ws = null, mediaRecorder = null, isRecording = false;

    function escapeHtml(str) {
        return String(str || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }

    function addStructuredResponse(text, data) {
        const div = document.createElement('div');
        div.className = 'msg assistant';
        let html = text ? `<p style="margin-bottom:10px;font-weight:500;">${escapeHtml(text)}</p>` : '';
        
        if (data.transactions) {
            html += '<div style="background:#fff; border-radius:12px; overflow:hidden; border:1px solid #e2e8f0; font-size:12px;">';
            data.transactions.forEach(t => {
                const date = new Date(t.date).toLocaleDateString('en-IN', {day:'2-digit', month:'short'});
                html += `<div style="padding:10px; border-bottom:1px solid #e2e8f0; display:flex; justify-content:space-between;">
                    <div><b>${escapeHtml(t.payee)}</b><br><span style="color:#64748b">${date}</span></div>
                    <div style="text-align:right;"><b>₹${t.amount}</b><br><span style="color:${t.status==='SUCCESS'?'#10b981':'#ef4444'}">${t.status}</span></div>
                </div>`;
            });
            html += '</div>';
        } else if (data.mandates) {
            html += '<div style="background:#fff; border-radius:12px; overflow:hidden; border:1px solid #e2e8f0; font-size:12px;">';
            data.mandates.forEach(m => {
                html += `<div style="padding:10px; border-bottom:1px solid #e2e8f0; display:flex; justify-content:space-between;">
                    <div><b>${escapeHtml(m.merchant)}</b></div>
                    <div style="text-align:right;"><b>₹${m.amount}/${m.frequency.toLowerCase()}</b><br><span style="color:#10b981">${m.status}</span></div>
                </div>`;
            });
            html += '</div>';
        }
        
        div.innerHTML = html;
        document.getElementById('chat-messages').appendChild(div);
        div.scrollIntoView({behavior: "smooth"});
    }

    function addDisambiguationOptions(text, optionsMap) {
        const div = document.createElement('div');
        div.className = 'msg assistant';
        let html = text ? `<p style="margin-bottom:10px;font-weight:500;">${escapeHtml(text)}</p>` : '';
        html += '<div style="display:flex; flex-direction:column; gap:8px; margin-top:10px;">';
        
        Object.keys(optionsMap).forEach(slotName => {
            optionsMap[slotName].forEach(opt => {
                // Attach slot_name to the option object so the backend knows which slot it resolves
                opt.slot_name = slotName;
                const optJson = escapeHtml(JSON.stringify(opt));
                html += `<button style="background:var(--primary); color:#fff; border:none; padding:10px 14px; border-radius:12px; font-weight:600; text-align:left; font-size:14px; cursor:pointer;" onclick="sendChatMessage(null, JSON.parse(this.dataset.opt))" data-opt="${optJson}">${escapeHtml(opt.resolved_label)}</button>`;
            });
        });
        html += '</div>';
        
        div.innerHTML = html;
        document.getElementById('chat-messages').appendChild(div);
        div.scrollIntoView({behavior: "smooth"});
    }

    async function toggleMic() {
        if (isRecording) { stopMic(); return; }
        if (!token || !convId) { alert('Please log in first.'); return; }
        
        const micBtn = document.getElementById('mic-btn');
        let stream;
        try {
            stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        } catch (e) {
            alert('Microphone permission denied.');
            return;
        }
        
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/api/voice?token=${encodeURIComponent(token)}&conversation_id=${encodeURIComponent(convId)}`;
        ws = new WebSocket(wsUrl);
        
        ws.onopen = async () => {
            micBtn.textContent = '⏹';
            micBtn.style.background = '#ef4444';
            isRecording = true;
            addChatMsg('user', '🎙️ Listening...');
            try {
                let options = {};
                if (typeof MediaRecorder.isTypeSupported === 'function' && MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) {
                    options = { mimeType: 'audio/webm;codecs=opus' };
                }
                mediaRecorder = new MediaRecorder(stream, options);
                mediaRecorder.ondataavailable = async (e) => {
                    if (e.data.size > 0 && ws && ws.readyState === WebSocket.OPEN) {
                        const buffer = await e.data.arrayBuffer();
                        ws.send(buffer);
                    }
                };
                mediaRecorder.onstop = () => {
                    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'stop_speaking' }));
                };
                mediaRecorder.start(250);
            } catch (e) {
                stopMic();
            }
        };
        
        ws.onmessage = async (e) => {
            if (typeof e.data !== 'string') return;
            const msg = JSON.parse(e.data);
            if (msg.type === 'transcript') {
                const msgs = document.getElementById('chat-messages');
                msgs.lastElementChild.innerHTML = '🎙️ ' + escapeHtml(msg.text);
            } else if (msg.type === 'response_text') {
                if (msg.disambiguation_options) {
                    addDisambiguationOptions(msg.text, msg.disambiguation_options);
                } else if (msg.response_data && (msg.response_data.transactions || msg.response_data.mandates)) {
                    addStructuredResponse(msg.text, msg.response_data);
                } else if (!msg.deep_link && !msg.confirmation_card) {
                    addChatMsg('assistant', msg.text);
                }
                if (msg.deep_link) {
                    addChatMsg('assistant', msg.text || "Please tap to proceed.");
                    handleDeepLink(msg.deep_link, msg.response_data && msg.response_data.requires_pin);
                }
                if (msg.confirmation_card) {
                    addActionCard("Confirm Action", msg.confirmation_card.text, "Confirm", () => {
                        sendChatMessage('__confirm__');
                    });
                }
            } else if (msg.type === 'done' || msg.type === 'tts_done') {
                micBtn.disabled = false;
            }
        };
        ws.onclose = () => { if(isRecording) stopMic(); }
    }
    
    function stopMic() {
        if (mediaRecorder && mediaRecorder.state !== 'inactive') mediaRecorder.stop();
        if (mediaRecorder && mediaRecorder.stream) mediaRecorder.stream.getTracks().forEach(t => t.stop());
        mediaRecorder = null;
        isRecording = false;
        const micBtn = document.getElementById('mic-btn');
        if(micBtn) {
            micBtn.textContent = '🎤';
            micBtn.style.background = '#10b981';
        }
    }

    async function openAppScreen(deepLink, pinRequired) {
        const screenPath = deepLink.replace('bhim://myupi', '/app') + '?token=' + token;
        let screenData = { screen: 'unknown', deep_link: deepLink };
        try {
            const r = await fetch(screenPath);
            if (r.ok) screenData = await r.json();
        } catch(e) {}
        screenData._pinRequired = pinRequired;
        
        // Use our action modal for this
        if (screenData.screen === 'safety_switch') {
            const modal = document.getElementById('action-modal');
            document.getElementById('action-modal-content').innerHTML = `
                <div style="font-weight:700; font-size:18px; margin-bottom:20px;">Safety Switch</div>
                <div style="margin-bottom:20px;">All outgoing payments are currently <b>${screenData.is_active ? 'Blocked' : 'Active'}</b>.</div>
                <button style="width:100%; padding:14px; background:var(--primary); color:#fff; border:none; border-radius:16px; font-weight:600;" onclick="document.getElementById('action-modal').style.display='none'; executeActionWithPin('Safety Switch', 'safety-switch', '1')">Toggle Safety Switch</button>
            `;
            modal.style.display = 'flex';
        } else if (screenData.screen === 'delink_number') {
            const modal = document.getElementById('action-modal');
            document.getElementById('action-modal-content').innerHTML = `
                <div style="font-weight:700; font-size:18px; margin-bottom:20px;">Delink Number</div>
                <div style="margin-bottom:20px;">Remove ${screenData.number} (${screenData.vpa})?</div>
                <button style="width:100%; padding:14px; background:#ef4444; color:#fff; border:none; border-radius:16px; font-weight:600;" onclick="executeActionWithPin('Delink Number', 'delink', '1')">Yes, Delink</button>
            `;
            modal.style.display = 'flex';
        } else {
            const modal = document.getElementById('action-modal');
            document.getElementById('action-modal-content').innerHTML = `
                <div style="font-weight:700; font-size:18px; margin-bottom:20px;">App Screen</div>
                <div style="margin-bottom:20px;">Navigated to: <b>${deepLink}</b></div>
                <div style="margin-bottom:20px; font-size:14px; color:#64748b;">This represents a native screen inside the BHIM app where you can complete this action manually.</div>
                <button style="width:100%; padding:14px; background:#e2e8f0; color:#333; border:none; border-radius:16px; font-weight:600;" onclick="document.getElementById('action-modal').style.display='none';">Close</button>
            `;
            modal.style.display = 'flex';
        }
    }

    async function doLogin() {
        const username = document.getElementById('login-user').value;
        const pin = document.getElementById('login-pin').value;
        try {
            const r = await fetch('/api/auth/login', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({username, pin})
            });
            if (!r.ok) throw new Error("Login failed");
            const data = await r.json();
            token = data.token;
            
            document.getElementById('login-screen').style.display = 'none';
            document.getElementById('main-header').style.display = 'flex';
            document.getElementById('main-content').style.display = 'block';
            document.getElementById('main-nav').style.display = 'flex';
            
            // Start chat session in background
            const r2 = await fetch('/api/conversations?token=' + token, {method: 'POST'});
            const data2 = await r2.json();
            convId = data2.conversation_id;
            
            // Initial load of data
            loadMandates();
            loadTransactions();
            
        } catch(e) {
            document.getElementById('login-error').textContent = e.message;
        }
    }

    function switchTab(tabId, el) {
        document.querySelectorAll('.tab-pane').forEach(t => t.classList.remove('active'));
        document.getElementById('tab-' + tabId).classList.add('active');
        
        document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
        el.classList.add('active');
        
        // Dynamic header styling
        const header = document.getElementById('main-header');
        if (tabId === 'ask-ai') {
            header.style.color = '#fff';
            header.querySelectorAll('button').forEach(b => b.style.color = '#fff');
            document.getElementById('main-content').style.background = 'linear-gradient(135deg, #fdfbfb 0%, #ebedee 100%)';
        } else {
            header.style.color = '#fff';
            document.getElementById('main-content').style.background = '#f8fafc';
        }
        
        if(tabId === 'autopay') loadMandates();
        if(tabId === 'transactions') loadTransactions();
    }

    async function loadMandates() {
        if(!token) return;
        const r = await fetch('/api/mandates?token=' + token);
        const data = await r.json();
        const list = document.getElementById('mandates-list');
        if(data.mandates.length === 0) {
            list.innerHTML = `<div class="white-card" style="text-align:center; padding: 24px; font-weight:500;">No active mandates found</div>`;
            return;
        }
        
        let html = '';
        data.mandates.forEach(m => {
            html += `
            <div class="white-card" style="padding:16px;" onclick="handleMandateClick('${m.id}', '${m.merchant}', '${m.status}')">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
                    <div style="font-weight:600; font-size:16px;">${m.merchant}</div>
                    <div style="font-weight:700; color:var(--text-dark);">₹${m.amount}</div>
                </div>
                <div style="display:flex; justify-content:space-between; align-items:center; font-size:13px; color:var(--text-muted);">
                    <div>${m.bank}</div>
                    <div style="background:${m.status === 'ACTIVE' ? '#dcfce7' : '#fef3c7'}; color:${m.status === 'ACTIVE' ? '#166534' : '#92400e'}; padding:4px 8px; border-radius:12px; font-weight:600; font-size:11px;">${m.status}</div>
                </div>
            </div>`;
        });
        list.innerHTML = html;
    }

    async function loadTransactions() {
        if(!token) return;
        const r = await fetch('/api/transactions?token=' + token);
        const data = await r.json();
        const list = document.getElementById('transactions-list');
        
        let html = '';
        data.transactions.forEach(t => {
            const date = new Date(t.date);
            const timeStr = date.toLocaleDateString('en-IN', {day:'numeric', month:'short'}) + ', ' + date.toLocaleTimeString('en-IN', {hour:'2-digit', minute:'2-digit'});
            
            html += `
            <div class="txn-row" onclick="handleTxnClick('${t.id}', '${t.payee}', '${t.amount}', ${t.eligible_chargeback})">
                <div class="txn-left">
                    <div class="txn-icon">↗</div>
                    <div class="txn-details">
                        <div class="txn-payee">Paid to ${t.payee}</div>
                        <div class="txn-time">${timeStr}</div>
                    </div>
                </div>
                <div class="txn-right">
                    <div class="txn-amount">₹${t.amount}</div>
                    <div class="txn-bank">DEBITED FROM ${t.bank.split(' ')[0]}</div>
                </div>
            </div>`;
        });
        list.innerHTML = html;
    }

    // Chat functionality
    function openChat(initialMsg = null) {
        document.getElementById('chat-screen').classList.add('open');
        if (initialMsg) {
            document.getElementById('real-chat-input').value = initialMsg;
            sendChatMessage();
        } else if (document.getElementById('chat-messages').children.length === 0) {
            addChatMsg('assistant', "Hi! I'm your MyUPI AI Assistant. You can tap the mic 🎤 or type your question below.");
        }
    }
    function closeChat() {
        document.getElementById('chat-screen').classList.remove('open');
    }

    function handleKey(e) {
        if (e.key === 'Enter') sendChatMessage();
    }

    function handleDeepLink(deepLink, requiresPin) {
        if (requiresPin) {
            addActionCard("Proceed with Action", "Requires UPI PIN to authenticate.", "Verify PIN", () => {
                requestPin("Authenticate Action", (pin) => {
                    const parts = deepLink.split('/');
                    let action = parts.pop();
                    let entityId = parts.pop();
                    if (action && !isNaN(action) && entityId === 'delink') {
                        entityId = action;
                        action = 'delink';
                    }
                    executeActionWithPin("Proceed", action, entityId, pin);
                });
            });
        } else {
            addActionCard("Action Required", "Continue to execute this action.", "Proceed", () => {
                openAppScreen(deepLink, false);
            });
        }
    }

    async function sendChatMessage(msgOverride = null, selectedEntity = null) {
        const input = document.getElementById('real-chat-input');
        const text = msgOverride || input.value.trim();
        if(!text && !selectedEntity) return;
        
        if (msgOverride !== '__confirm__' && !selectedEntity) {
            input.value = '';
            addChatMsg('user', text);
        } else if (selectedEntity) {
            addChatMsg('user', selectedEntity.resolved_label);
        }

        if (!token || !convId) {
            addChatMsg('assistant', '⚠️ Please log in first.');
            return;
        }

        const payload = { token, conversation_id: convId, message: text, user_confirmed: (msgOverride==='__confirm__') };
        if (selectedEntity) payload.selected_entity = selectedEntity;

        const r = await fetch('/api/chat', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        let data;
        try {
            if (!r.ok) {
                const errData = await r.json();
                throw new Error(errData.detail || "Server error");
            }
            data = await r.json();
        } catch(e) {
            addChatMsg('assistant', '⚠️ ' + e.message);
            return;
        }

        if (data.error) {
            addChatMsg('assistant', '⚠️ ' + data.error);
        } else if (data.disambiguation_options) {
            addDisambiguationOptions(data.user_prompt || data.response_text, data.disambiguation_options);
        } else if (data.response_data && (data.response_data.transactions || data.response_data.mandates)) {
            addStructuredResponse(data.response_text || data.user_prompt, data.response_data);
        } else if (data.user_prompt) {
            addChatMsg('assistant', data.user_prompt);
        } else if (data.response_text && !data.deep_link) {
            addChatMsg('assistant', data.response_text);
        }
        
        if (data.deep_link) {
            addChatMsg('assistant', data.response_text || data.user_prompt || "Please tap to proceed.");
            handleDeepLink(data.deep_link, data.response_data && data.response_data.requires_pin);
        }
        
        if (data.confirmation_card) {
            addActionCard("Confirm Action", data.confirmation_card.text, "Confirm", () => {
                sendChatMessage('__confirm__');
            });
        }
    }
    
    function addChatMsg(role, text) {
        const div = document.createElement('div');
        div.className = 'msg ' + role;
        div.innerHTML = marked.parse(text);
        document.getElementById('chat-messages').appendChild(div);
        div.scrollIntoView({behavior: "smooth"});
    }
    
    function addActionCard(title, text, btnText, callback) {
        const div = document.createElement('div');
        div.className = 'msg assistant';
        div.innerHTML = `<div style="font-weight:600; margin-bottom:8px;">${title}</div><div style="font-size:13px; margin-bottom:12px;">${text}</div><button style="background:var(--primary); color:#fff; border:none; padding:8px 16px; border-radius:16px; font-weight:600;">${btnText}</button>`;
        div.querySelector('button').onclick = callback;
        document.getElementById('chat-messages').appendChild(div);
        div.scrollIntoView({behavior: "smooth"});
    }

    // Modal Actions
    function handleMandateClick(id, merchant, status) {
        const modal = document.getElementById('action-modal');
        const content = document.getElementById('action-modal-content');
        let actions = '';
        if (status === 'ACTIVE') {
            actions = `<button style="width:100%; padding:14px; background:#f59e0b; color:#fff; border:none; border-radius:16px; font-weight:600; margin-bottom:10px;" onclick="executeActionWithPin('Pause AutoPay', 'pause', '${id}')">⏸ Pause AutoPay</button>
                       <button style="width:100%; padding:14px; background:#ef4444; color:#fff; border:none; border-radius:16px; font-weight:600;" onclick="executeActionWithPin('Revoke AutoPay', 'revoke', '${id}')">🚫 Revoke AutoPay</button>`;
        } else {
            actions = `<button style="width:100%; padding:14px; background:var(--primary); color:#fff; border:none; border-radius:16px; font-weight:600; margin-bottom:10px;" onclick="executeActionWithPin('Resume AutoPay', 'resume', '${id}')">▶️ Resume AutoPay</button>
                       <button style="width:100%; padding:14px; background:#ef4444; color:#fff; border:none; border-radius:16px; font-weight:600;" onclick="executeActionWithPin('Revoke AutoPay', 'revoke', '${id}')">🚫 Revoke AutoPay</button>`;
        }
        
        content.innerHTML = `
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:20px;">
                <div style="font-weight:700; font-size:18px;">Manage ${merchant}</div>
                <button onclick="document.getElementById('action-modal').style.display='none'" style="background:none; border:none; font-size:20px;">✕</button>
            </div>
            ${actions}
        `;
        modal.style.display = 'flex';
    }
    
    function handleTxnClick(id, payee, amount, eligible) {
        const modal = document.getElementById('action-modal');
        const content = document.getElementById('action-modal-content');
        
        let chargebackBtn = eligible 
            ? `<button style="width:100%; padding:14px; background:#ef4444; color:#fff; border:none; border-radius:16px; font-weight:600;" onclick="executeActionWithPin('Dispute Payment', 'chargeback', '${id}')">⚠️ Raise Dispute (Chargeback)</button>`
            : `<div style="padding:12px; background:#f1f5f9; color:var(--text-muted); font-size:13px; text-align:center; border-radius:12px;">This transaction is not eligible for dispute (P2P or too old).</div>`;
            
        content.innerHTML = `
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:20px;">
                <div style="font-weight:700; font-size:18px;">Transaction Options</div>
                <button onclick="document.getElementById('action-modal').style.display='none'" style="background:none; border:none; font-size:20px;">✕</button>
            </div>
            <div style="font-weight:600; margin-bottom:20px;">Paid to ${payee} (₹${amount})</div>
            ${chargebackBtn}
        `;
        modal.style.display = 'flex';
    }

    // PIN Handling
    let currentPinCallback = null;
    let typedPin = '';
    
    function requestPin(subtext, callback) {
        document.getElementById('action-modal').style.display = 'none';
        document.getElementById('pin-screen').style.display = 'flex';
        document.getElementById('pin-subtext').innerText = subtext;
        currentPinCallback = callback;
        typedPin = '';
        updatePinDots();
    }
    
    function typePin(digit) {
        if(typedPin.length < 4) {
            typedPin += digit;
            updatePinDots();
        }
    }
    function clearPin() {
        if(typedPin.length > 0) {
            typedPin = typedPin.slice(0, -1);
            updatePinDots();
        }
    }
    function updatePinDots() {
        const dots = document.querySelectorAll('.pin-dot');
        dots.forEach((dot, i) => {
            if(i < typedPin.length) dot.classList.add('filled');
            else dot.classList.remove('filled');
        });
    }
    function cancelPin() {
        document.getElementById('pin-screen').style.display = 'none';
    }
    function submitPin() {
        if(typedPin.length === 4) {
            document.getElementById('pin-screen').style.display = 'none';
            if(currentPinCallback) currentPinCallback(typedPin);
        }
    }

    function executeActionWithPin(actionName, actionType, id) {
        requestPin(actionName, async (pin) => {
            // API call to simulate action execution
            try {
                await fetch('/api/execute', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({token, action: actionType, id: id})
                });
                alert(`✅ ${actionName} executed successfully!`);
                // Reload data
                loadMandates();
                loadTransactions();
            } catch(e) {
                alert("Action failed.");
            }
        });
    }

    function verifyPayeeAction() {
        const v = document.getElementById('verify-payee-input').value.trim();
        if(v) {
            openChat(`Verify payee ${v}`);
        } else {
            alert("Please enter a UPI ID or phone number to verify");
        }
    }

