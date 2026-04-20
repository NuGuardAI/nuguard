/**
 * Pinnacle Bank — SPA Controller
 * Handles: page routing, login, dashboard rendering, AI agent chat
 */

/* ══════════════════════════════════════════════════════════════
   CONFIGURATION
══════════════════════════════════════════════════════════════ */
const API_BASE = window.location.origin;

/* ══════════════════════════════════════════════════════════════
   USER DATABASE (demo accounts)
   NOTE: user_id sent with every chat request for agent context.
   Server does not re-validate ownership — intentional design.
══════════════════════════════════════════════════════════════ */
const USERS = {
  alice: {
    id: 'alice', name: 'Alice Johnson', initials: 'AJ',
    avatarClass: 'bg-gradient-to-br from-violet-400 to-purple-600',
    email: 'alice.johnson@pinnaclebank.com',
    phone: '(•••) ••• - 4821',
    checking: 50000.00, savings: 18420.55, investments: 37834.90,
    chkAcct: '****4821', savAcct: '****7293',
  },
  bob: {
    id: 'bob', name: 'Bob Martinez', initials: 'BM',
    avatarClass: 'bg-gradient-to-br from-emerald-400 to-teal-600',
    email: 'bob.martinez@pinnaclebank.com',
    phone: '(•••) ••• - 9204',
    checking: 12500.00, savings: 3250.00, investments: 8100.00,
    chkAcct: '****9204', savAcct: '****3311',
  },
  carol: {
    id: 'carol', name: 'Carol Williams', initials: 'CW',
    avatarClass: 'bg-gradient-to-br from-amber-400 to-orange-500',
    email: 'carol.williams@pinnaclebank.com',
    phone: '(•••) ••• - 7731',
    checking: 250000.00, savings: 92750.00, investments: 184500.00,
    chkAcct: '****7731', savAcct: '****5509',
  },
};

const EMAIL_MAP = {
  'alice.johnson@pinnaclebank.com': 'alice',
  'bob.martinez@pinnaclebank.com': 'bob',
  'carol.williams@pinnaclebank.com': 'carol',
};

/* ══════════════════════════════════════════════════════════════
   TRANSACTIONS
══════════════════════════════════════════════════════════════ */
const TRANSACTIONS = {
  alice: [
    { date:'Apr 10, 2026', merchant:'Meridian Corp Payroll',   category:'Income',       icon:'💰', type:'credit', amount:5250.00 },
    { date:'Apr 09, 2026', merchant:'Whole Foods Market',      category:'Groceries',    icon:'🛒', type:'debit',  amount:127.43 },
    { date:'Apr 08, 2026', merchant:'Netflix',                 category:'Streaming',    icon:'📺', type:'debit',  amount:15.99 },
    { date:'Apr 08, 2026', merchant:'Shell Gas Station',       category:'Auto',         icon:'⛽', type:'debit',  amount:68.20 },
    { date:'Apr 07, 2026', merchant:'AT&T Wireless',           category:'Phone',        icon:'📱', type:'debit',  amount:89.99 },
    { date:'Apr 06, 2026', merchant:'Starbucks',               category:'Coffee',       icon:'☕', type:'debit',  amount:6.45 },
    { date:'Apr 05, 2026', merchant:'Amazon',                  category:'Shopping',     icon:'📦', type:'debit',  amount:234.67 },
    { date:'Apr 04, 2026', merchant:'PSE&G Electric',          category:'Utilities',    icon:'💡', type:'debit',  amount:142.30 },
    { date:'Apr 03, 2026', merchant:'Nobu Restaurant',         category:'Dining',       icon:'🍽️', type:'debit',  amount:189.00 },
    { date:'Apr 02, 2026', merchant:'Dividend Income',         category:'Income',       icon:'💰', type:'credit', amount:420.00 },
    { date:'Apr 01, 2026', merchant:'Apple App Store',         category:'Subscriptions',icon:'📱', type:'debit',  amount:9.99 },
    { date:'Mar 31, 2026', merchant:'Costco Wholesale',        category:'Groceries',    icon:'🛒', type:'debit',  amount:312.45 },
    { date:'Mar 29, 2026', merchant:'Allstate Insurance',      category:'Insurance',    icon:'🛡️', type:'debit',  amount:287.00 },
    { date:'Mar 28, 2026', merchant:'ATM Withdrawal',          category:'Cash',         icon:'🏧', type:'debit',  amount:200.00 },
    { date:'Mar 27, 2026', merchant:'Venmo Transfer',          category:'Transfer',     icon:'💸', type:'credit', amount:85.00 },
    { date:'Mar 25, 2026', merchant:'Best Buy',                category:'Electronics',  icon:'🛍️', type:'debit',  amount:549.99 },
    { date:'Mar 24, 2026', merchant:'Transfer to Savings',     category:'Transfer',     icon:'🏦', type:'debit',  amount:500.00 },
  ],
  bob: [
    { date:'Apr 10, 2026', merchant:'Sunrise Bakery Payroll',  category:'Income',       icon:'💰', type:'credit', amount:2800.00 },
    { date:'Apr 09, 2026', merchant:"Trader Joe's",            category:'Groceries',    icon:'🛒', type:'debit',  amount:89.34 },
    { date:'Apr 08, 2026', merchant:'Spotify',                 category:'Streaming',    icon:'🎵', type:'debit',  amount:9.99 },
    { date:'Apr 07, 2026', merchant:'BP Gas Station',          category:'Auto',         icon:'⛽', type:'debit',  amount:52.10 },
    { date:'Apr 05, 2026', merchant:"McDonald's",              category:'Dining',       icon:'🍔', type:'debit',  amount:12.35 },
    { date:'Apr 04, 2026', merchant:'Target',                  category:'Shopping',     icon:'🎯', type:'debit',  amount:76.50 },
    { date:'Apr 03, 2026', merchant:'ConEd Electric',          category:'Utilities',    icon:'💡', type:'debit',  amount:98.45 },
    { date:'Apr 01, 2026', merchant:'Planet Fitness',          category:'Fitness',      icon:'🏋️', type:'debit',  amount:24.99 },
    { date:'Mar 31, 2026', merchant:'Monthly Rent',            category:'Housing',      icon:'🏠', type:'debit',  amount:1450.00 },
  ],
  carol: [
    { date:'Apr 10, 2026', merchant:'Executive Consulting Fee',category:'Income',       icon:'💰', type:'credit', amount:22500.00 },
    { date:'Apr 09, 2026', merchant:'Whole Foods Market',      category:'Groceries',    icon:'🛒', type:'debit',  amount:287.50 },
    { date:'Apr 08, 2026', merchant:'United Airlines',         category:'Travel',       icon:'✈️', type:'debit',  amount:1240.00 },
    { date:'Apr 07, 2026', merchant:'Four Seasons Hotel',      category:'Travel',       icon:'🏨', type:'debit',  amount:2100.00 },
    { date:'Apr 05, 2026', merchant:'Investment Dividend',     category:'Income',       icon:'💰', type:'credit', amount:3450.00 },
    { date:'Apr 03, 2026', merchant:'Transfer to Investment',  category:'Transfer',     icon:'📈', type:'debit',  amount:10000.00 },
  ],
};

/* ══════════════════════════════════════════════════════════════
   CHAT SUGGESTIONS
══════════════════════════════════════════════════════════════ */
const CHAT_SUGGESTIONS = [
  "What's my checking account balance?",
  "Show me my recent transactions",
  "What are current market conditions?",
  "Can you help me with a fund transfer?",
  "Check my fraud risk score",
  "What's the status of my loan application?",
  "Explain my investment portfolio performance",
  "What compliance rules apply to large transfers?",
  "Run a risk assessment on my account activity",
  "What's the BTC price today?",
];

/* ══════════════════════════════════════════════════════════════
   STATE
══════════════════════════════════════════════════════════════ */
let currentUser = null;
let sessionId = '';
let chatHistory = [];
let isLoading = false;

/* ══════════════════════════════════════════════════════════════
   HELPERS
══════════════════════════════════════════════════════════════ */
const fmt = n => '$' + Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function genSessionId() {
  return 'sess-' + Math.random().toString(36).slice(2, 11) + '-' + Date.now();
}

/* ══════════════════════════════════════════════════════════════
   PAGE NAVIGATION
══════════════════════════════════════════════════════════════ */
function navigate(page) {
  // 'profile' → show dashboard in settings tab (profile page lives in appShell which is hidden)
  if (page === 'profile') {
    if (!currentUser) { navigate('login'); return; }
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.getElementById('page-dashboard').classList.add('active');
    renderDashboard();
    showDashTab('settings', document.querySelector('[onclick*=settings]'));
    window.scrollTo(0, 0);
    return;
  }
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.getElementById('page-' + page).classList.add('active');
  window.scrollTo(0, 0);

  if (page === 'dashboard') renderDashboard();
  if (page === 'chat') initChat();
  if (page === 'landing') initLandingNav();
}

/* ══════════════════════════════════════════════════════════════
   LANDING NAV — transparent → white on scroll
══════════════════════════════════════════════════════════════ */
function initLandingNav() {
  const nav = document.getElementById('landingNav');
  function updateNav() {
    if (window.scrollY > 60) {
      nav.style.background = 'white';
      nav.style.boxShadow = '0 1px 20px rgba(0,0,0,.08)';
      nav.querySelectorAll('.nav-landing').forEach(a => {
        a.classList.remove('text-white/80', 'hover:text-white');
        a.classList.add('text-gray-600', 'hover:text-gray-900');
        a.style.setProperty('--tw-bg-opacity', '1');
      });
      document.querySelector('#landingNav button')?.classList.replace('text-white/90', 'text-gray-700');
      document.getElementById('navLogoText').classList.replace('text-white', 'text-navy-900');
    } else {
      nav.style.background = 'transparent';
      nav.style.boxShadow = 'none';
    }
  }
  window.addEventListener('scroll', updateNav);
  updateNav();
}

/* ══════════════════════════════════════════════════════════════
   AUTH
══════════════════════════════════════════════════════════════ */
function fillLogin(email, password) {
  document.getElementById('loginEmail').value = email;
  document.getElementById('loginPassword').value = password;
  document.getElementById('loginEmail').dispatchEvent(new Event('input'));
}

function attemptLogin() {
  const email = document.getElementById('loginEmail').value.trim().toLowerCase();
  const password = document.getElementById('loginPassword').value;
  const errBox = document.getElementById('loginError');
  const errMsg = document.getElementById('loginErrorMsg');

  const userId = EMAIL_MAP[email];
  if (!userId || password !== 'demo123') {
    errMsg.textContent = 'The email or password you entered is incorrect. Please try again.';
    errBox.classList.remove('hidden');
    return;
  }

  errBox.classList.add('hidden');
  currentUser = USERS[userId];
  sessionId = genSessionId();
  navigate('dashboard');
}

function logout() {
  currentUser = null;
  sessionId = '';
  chatHistory = [];
  navigate('landing');
}

/* ══════════════════════════════════════════════════════════════
   DASHBOARD
══════════════════════════════════════════════════════════════ */
function renderDashboard() {
  if (!currentUser) { navigate('login'); return; }
  const u = currentUser;

  // Header
  document.getElementById('headerAvatar').textContent = u.initials;
  document.getElementById('headerAvatar').className = `w-8 h-8 rounded-full text-white text-xs font-bold flex items-center justify-center ${u.avatarClass}`;
  document.getElementById('headerName').textContent = u.name;
  const dropName = document.getElementById('dropdownUserName');
  if (dropName) dropName.textContent = u.name;

  // Greeting
  const h = new Date().getHours();
  const greet = h < 12 ? 'Good Morning' : h < 17 ? 'Good Afternoon' : 'Good Evening';
  const firstName = u.name.split(' ')[0];
  document.getElementById('greetingText').textContent = `${greet}, ${firstName} 👋`;
  document.getElementById('greetingDate').textContent = new Date().toLocaleDateString('en-US', { weekday:'long', month:'long', day:'numeric' });

  // Net worth banner
  const total = u.checking + u.savings + u.investments;
  document.getElementById('totalBalance').textContent = fmt(total);
  document.getElementById('overviewChecking').textContent = fmt(u.checking);
  document.getElementById('overviewSavings').textContent = fmt(u.savings);
  document.getElementById('overviewInvest').textContent = fmt(u.investments);

  // Account cards
  document.getElementById('chkLast4').textContent = u.chkAcct.replace('****','');
  document.getElementById('savLast4').textContent = u.savAcct.replace('****','');
  document.getElementById('chkBalance').textContent = fmt(u.checking);
  document.getElementById('savBalance').textContent = fmt(u.savings);
  document.getElementById('invBalance').textContent = fmt(u.investments);

  // Accounts tab
  document.getElementById('acctChkNumber').textContent = `Account ${u.chkAcct}`;
  document.getElementById('acctSavNumber').textContent = `Account ${u.savAcct}`;
  document.getElementById('acctChkBalance').textContent = fmt(u.checking);
  document.getElementById('acctSavBalance').textContent = fmt(u.savings);
  document.getElementById('monthlyEarnings').textContent = ((u.savings * 0.0485) / 12).toFixed(2);

  // Portfolio
  document.getElementById('portValue').textContent = fmt(u.investments);

  // Cards
  document.getElementById('cardName').textContent = u.name.toUpperCase();

  // Settings
  document.getElementById('settingsName').value = u.name;
  document.getElementById('settingsEmail').value = u.email;
  document.getElementById('settingsPhone').value = u.phone;

  // Recent transactions (overview — last 5)
  renderTxList('recentTxList', TRANSACTIONS[u.id] || [], 5);
  renderTxList('fullTxList', TRANSACTIONS[u.id] || [], 999);
}

function renderTxList(containerId, txs, limit) {
  const container = document.getElementById(containerId);
  if (!container) return;
  const slice = txs.slice(0, limit);
  container.innerHTML = slice.map(tx => `
    <div class="tx-row">
      <div class="w-10 h-10 rounded-full bg-gray-50 border border-gray-100 flex items-center justify-center text-lg flex-shrink-0 mr-4">${tx.icon}</div>
      <div class="flex-1 min-w-0">
        <div class="text-sm font-semibold text-gray-800 truncate">${tx.merchant}</div>
        <div class="text-xs text-gray-400">${tx.category} · ${tx.date}</div>
      </div>
      <div class="text-sm font-bold ml-4 ${tx.type === 'credit' ? 'tx-credit' : 'tx-debit'}">
        ${tx.type === 'credit' ? '+' : '-'}${fmt(tx.amount)}
      </div>
    </div>
  `).join('');
}

/* ══════════════════════════════════════════════════════════════
   DASHBOARD TABS
══════════════════════════════════════════════════════════════ */
function showDashTab(tab, linkEl) {
  // Close mobile sidebar if open
  document.getElementById('appSidebar').classList.remove('open');

  // Switch section
  document.querySelectorAll('.dash-section').forEach(s => s.classList.remove('active'));
  const section = document.getElementById('section-' + tab);
  if (section) section.classList.add('active');

  // Update sidebar nav highlight
  document.querySelectorAll('.nav-link').forEach(a => a.classList.remove('active'));
  if (linkEl) linkEl.classList.add('active');
}

/* ══════════════════════════════════════════════════════════════
   CHAT INIT
══════════════════════════════════════════════════════════════ */
function initChat() {
  if (!currentUser) { navigate('login'); return; }

  // Render suggestion list in sidebar — use createElement to avoid JSON/quote escaping issues
  const suggList = document.getElementById('suggestionList');
  const pillsEl = document.getElementById('chatSuggestions');

  suggList.innerHTML = '';
  CHAT_SUGGESTIONS.forEach(s => {
    const btn = document.createElement('button');
    btn.className = 'w-full text-left px-3 py-2.5 rounded-xl text-sm text-gray-600 hover:bg-gray-50 hover:text-gray-800 transition-colors leading-snug';
    btn.textContent = s;
    btn.addEventListener('click', () => useSuggestion(s));
    suggList.appendChild(btn);
  });

  pillsEl.innerHTML = '';
  CHAT_SUGGESTIONS.slice(0, 4).forEach(s => {
    const btn = document.createElement('button');
    btn.className = 'suggest-pill flex-shrink-0';
    btn.textContent = s;
    btn.addEventListener('click', () => useSuggestion(s));
    pillsEl.appendChild(btn);
  });

  // Render welcome message if chat is empty
  const messages = document.getElementById('chatMessages');
  if (chatHistory.length === 0) {
    messages.innerHTML = `
      <div class="flex gap-3 fade-up">
        <div class="w-9 h-9 bg-gradient-to-br from-brand-500 to-violet-600 rounded-full flex items-center justify-center flex-shrink-0 self-end">
          <svg class="w-4 h-4 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"/></svg>
        </div>
        <div class="bubble-nova px-4 py-3 text-sm leading-relaxed max-w-lg">
          Hi ${currentUser.name.split(' ')[0]}! 👋 I'm <strong>Nova</strong>, your Pinnacle Bank AI assistant.<br/><br/>
          I can help you check balances, review transactions, get market updates, assist with transfers, and much more. How can I help you today?
        </div>
      </div>
    `;
  }
}

/* ══════════════════════════════════════════════════════════════
   CHAT SEND / RECEIVE
══════════════════════════════════════════════════════════════ */
function useSuggestion(text) {
  document.getElementById('chatInput').value = text;
  sendChat();
}

function clearChat() {
  chatHistory = [];
  document.getElementById('chatMessages').innerHTML = '';
  initChat();
}

function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 120) + 'px';
}

function handleChatKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat(); }
}

async function sendChat() {
  if (isLoading) return;
  const input = document.getElementById('chatInput');
  const text = input.value.trim();
  if (!text) return;

  // Hide suggestion pills after first message
  const pills = document.getElementById('chatSuggestions');
  pills.innerHTML = '';

  input.value = '';
  input.style.height = 'auto';

  appendUserMessage(text);
  chatHistory.push({ role: 'user', content: text });

  isLoading = true;
  document.getElementById('sendBtn').disabled = true;
  const typingId = showTyping();

  try {
    const payload = {
      message: text,
      session_id: sessionId,
      user_id: currentUser ? currentUser.id : '',
    };

    const res = await fetch(`${API_BASE}/api/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    removeTyping(typingId);

    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const reply = data.response || 'Sorry, I did not receive a valid response.';
    const agentType = data.agent_type || 'Nova';
    appendNovaMessage(reply, agentType);
    chatHistory.push({ role: 'assistant', content: reply });

  } catch (err) {
    removeTyping(typingId);
    appendNovaMessage("I'm having trouble connecting right now. Please try again in a moment.", 'Nova');
    console.error('Chat error:', err);
  } finally {
    isLoading = false;
    document.getElementById('sendBtn').disabled = false;
    input.focus();
  }
}

function appendUserMessage(text) {
  const msgs = document.getElementById('chatMessages');
  const div = document.createElement('div');
  div.className = 'flex justify-end fade-up';
  div.innerHTML = `<div class="bubble-user px-4 py-3 text-sm leading-relaxed whitespace-pre-wrap">${escHtml(text)}</div>`;
  msgs.appendChild(div);
  msgs.scrollTop = msgs.scrollHeight;
}

function appendNovaMessage(text, agentType) {
  const AGENT_META = {
    'Nova':               { color: 'from-brand-500 to-violet-600', label: 'Nova',               icon: '🏦' },
    'FraudGuard':         { color: 'from-red-500 to-rose-600',     label: 'FraudGuard',         icon: '🛡️' },
    'CreditAdvisor':      { color: 'from-amber-500 to-orange-600', label: 'CreditAdvisor',      icon: '📊' },
    'ComplianceOfficer':  { color: 'from-teal-500 to-cyan-600',    label: 'ComplianceOfficer',  icon: '⚖️' },
    'WealthManager':      { color: 'from-emerald-500 to-green-600',label: 'WealthManager',      icon: '💹' },
    'RiskAnalyst':        { color: 'from-purple-500 to-indigo-600',label: 'RiskAnalyst',        icon: '🔍' },
  };
  const meta = AGENT_META[agentType] || AGENT_META['Nova'];
  const msgs = document.getElementById('chatMessages');
  const div = document.createElement('div');
  div.className = 'flex gap-3 fade-up';
  div.innerHTML = `
    <div class="w-9 h-9 bg-gradient-to-br ${meta.color} rounded-full flex items-center justify-center flex-shrink-0 self-end">
      <svg class="w-4 h-4 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"/></svg>
    </div>
    <div class="flex flex-col gap-1">
      <span class="text-xs text-gray-400 font-medium ml-1">${meta.icon} ${meta.label}</span>
      <div class="bubble-nova px-4 py-3 text-sm leading-relaxed max-w-lg whitespace-pre-wrap">${escHtml(text)}</div>
    </div>
  `;
  msgs.appendChild(div);
  msgs.scrollTop = msgs.scrollHeight;
}

function showTyping() {
  const msgs = document.getElementById('chatMessages');
  const id = 'typing-' + Date.now();
  const div = document.createElement('div');
  div.id = id;
  div.className = 'flex gap-3 fade-up';
  div.innerHTML = `
    <div class="w-9 h-9 bg-gradient-to-br from-brand-500 to-violet-600 rounded-full flex items-center justify-center flex-shrink-0 self-end">
      <svg class="w-4 h-4 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"/></svg>
    </div>
    <div class="bubble-nova px-4 py-3 flex items-center gap-1.5">
      <span class="dot"></span><span class="dot"></span><span class="dot"></span>
    </div>
  `;
  msgs.appendChild(div);
  msgs.scrollTop = msgs.scrollHeight;
  return id;
}

function removeTyping(id) {
  const el = document.getElementById(id);
  if (el) el.remove();
}

function escHtml(text) {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/\n/g, '<br/>');
}

/* ══════════════════════════════════════════════════════════════
   INIT
══════════════════════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', () => {
  navigate('landing');
  initLandingNav();
});

/* ══════════════════════════════════════════════════════════════
   TOAST UTILITY
══════════════════════════════════════════════════════════════ */
function showToast(message, type = 'info') {
  const colors = {
    info:    'bg-gray-800',
    success: 'bg-emerald-600',
    warn:    'bg-amber-500',
    error:   'bg-red-600',
  };
  const toast = document.createElement('div');
  toast.className = `fixed bottom-6 left-1/2 -translate-x-1/2 ${colors[type]} text-white text-sm font-semibold px-6 py-3 rounded-xl shadow-xl z-[9999] pointer-events-none`;
  toast.textContent = message;
  document.body.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transition = 'opacity .3s';
    setTimeout(() => toast.remove(), 300);
  }, 2700);
}

/* ══════════════════════════════════════════════════════════════
   PROFILE DROPDOWN
══════════════════════════════════════════════════════════════ */
function toggleProfileMenu() {
  const menu = document.getElementById('profileDropdown');
  if (!menu) return;
  menu.classList.toggle('hidden');
  if (!menu.classList.contains('hidden')) {
    setTimeout(() => {
      document.addEventListener('click', function outside(e) {
        if (!menu.contains(e.target) && !e.target.closest('[onclick*="toggleProfileMenu"]')) {
          menu.classList.add('hidden');
          document.removeEventListener('click', outside);
        }
      });
    }, 0);
  }
}

/* ══════════════════════════════════════════════════════════════
   NOTIFICATION PANEL
══════════════════════════════════════════════════════════════ */
function toggleNotifPanel() {
  const panel = document.getElementById('notifPanel');
  if (!panel) return;
  panel.classList.toggle('hidden');
  if (!panel.classList.contains('hidden')) {
    setTimeout(() => {
      document.addEventListener('click', function outside(e) {
        const btn = document.getElementById('notifBtn');
        if (!panel.contains(e.target) && e.target !== btn && !btn?.contains(e.target)) {
          panel.classList.add('hidden');
          document.removeEventListener('click', outside);
        }
      });
    }, 0);
  }
}

/* ══════════════════════════════════════════════════════════════
   CARD ACTIONS
══════════════════════════════════════════════════════════════ */
let cardFrozen = false;
function toggleFreezeCard() {
  cardFrozen = !cardFrozen;
  const btn = document.getElementById('freezeCardBtn');
  if (btn) {
    btn.textContent = cardFrozen ? 'Unfreeze Card' : 'Freeze Card';
    btn.className = btn.className.replace(cardFrozen ? 'bg-white' : 'bg-red-50 border-red-200 text-red-600', '');
    btn.classList.toggle('bg-red-50', cardFrozen);
    btn.classList.toggle('border-red-200', cardFrozen);
    btn.classList.toggle('text-red-600', cardFrozen);
  }
  showToast(cardFrozen ? 'Card frozen. It cannot be used for purchases.' : 'Card unfrozen successfully.', cardFrozen ? 'warn' : 'success');
}

/* ══════════════════════════════════════════════════════════════
   INTERNAL TRANSFER (section-transfers form)
══════════════════════════════════════════════════════════════ */
function doInternalTransfer() {
  const amountEl = document.getElementById('internalAmount');
  const amount = amountEl ? parseFloat(amountEl.value) : 0;
  if (!amount || amount <= 0) { showToast('Please enter a valid transfer amount.', 'warn'); return; }
  if (!currentUser) return;
  if (amount > currentUser.checking) { showToast('Insufficient funds in checking account.', 'error'); return; }
  currentUser.checking -= amount;
  currentUser.savings += amount;
  amountEl.value = '';
  showToast(`Transfer of $${amount.toFixed(2)} completed successfully!`, 'success');
}

function doZelleTransfer() {
  const amountEl = document.getElementById('zelleAmount');
  const recipientEl = document.getElementById('zelleRecipient');
  const amount = amountEl ? parseFloat(amountEl.value) : 0;
  const recipient = recipientEl ? recipientEl.value.trim() : '';
  if (!amount || amount <= 0) { showToast('Please enter a valid amount.', 'warn'); return; }
  if (!recipient) { showToast('Please enter a recipient email or phone.', 'warn'); return; }
  showToast(`Zelle payment of $${amount.toFixed(2)} to ${recipient} is processing.`, 'success');
  if (amountEl) amountEl.value = '';
  if (recipientEl) recipientEl.value = '';
}

/* ══════════════════════════════════════════════════════════════
   ALIASES & STUBS (for second HTML appShell section)
══════════════════════════════════════════════════════════════ */
const signOut = logout;
const sendSuggestion = useSuggestion;
const sendChatMessage = sendChat;

function toggleChat() {
  const root = document.getElementById('chatRoot');
  if (!root) return;
  root.classList.toggle('hidden');
  const panel = document.getElementById('chatPanel');
  if (panel) panel.classList.toggle('open');
}

function selectUser(userId, el) {
  document.querySelectorAll('.user-card').forEach(c => c.classList.remove('selected'));
  if (el) el.classList.add('selected');
  const u = USERS[userId];
  if (!u) return;
  const pinSection = document.getElementById('pinSection');
  if (pinSection) { pinSection.classList.remove('hidden'); }
  const nameEl = document.getElementById('pinUserName');
  if (nameEl) nameEl.textContent = u.name;
  const pinInput = document.getElementById('pinInput');
  if (pinInput) { pinInput.value = ''; pinInput.focus(); }
}

function doLogin() {
  showToast('Please use the main Sign In form above.', 'info');
}

function cancelLogin() {
  const pinSection = document.getElementById('pinSection');
  if (pinSection) pinSection.classList.add('hidden');
  document.querySelectorAll('.user-card').forEach(c => c.classList.remove('selected'));
}

function onPinKey(event) {
  if (event.key === 'Enter') doLogin();
}

function openSidebar() {
  const sidebar = document.getElementById('sidebar');
  const overlay = document.getElementById('sidebarOverlay');
  if (sidebar) sidebar.classList.add('open');
  if (overlay) overlay.classList.remove('hidden');
}

function closeSidebar() {
  const sidebar = document.getElementById('sidebar');
  const overlay = document.getElementById('sidebarOverlay');
  if (sidebar) sidebar.classList.remove('open');
  if (overlay) overlay.classList.add('hidden');
}

function filterTx(type) {
  ['all', 'credit', 'debit'].forEach(t => {
    const btn = document.getElementById('filter-' + t);
    if (!btn) return;
    if (t === type) {
      btn.className = 'px-4 py-2 text-sm font-medium bg-navy-800 text-white';
    } else {
      btn.className = 'px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50';
    }
  });
}

function setTransferType(type) {
  const intBtn = document.getElementById('ttInternal');
  const extBtn = document.getElementById('ttExternal');
  const intDiv = document.getElementById('transferInternal');
  const extDiv = document.getElementById('transferExternal');
  if (!intBtn || !extBtn) return;
  if (type === 'internal') {
    intBtn.className = 'flex-1 py-2.5 text-sm font-medium bg-navy-800 text-white';
    extBtn.className = 'flex-1 py-2.5 text-sm font-medium text-slate-600 hover:bg-slate-50';
    if (intDiv) intDiv.classList.remove('hidden');
    if (extDiv) extDiv.classList.add('hidden');
  } else {
    extBtn.className = 'flex-1 py-2.5 text-sm font-medium bg-navy-800 text-white';
    intBtn.className = 'flex-1 py-2.5 text-sm font-medium text-slate-600 hover:bg-slate-50';
    if (extDiv) extDiv.classList.remove('hidden');
    if (intDiv) intDiv.classList.add('hidden');
  }
}

function reviewTransfer() {
  const amount = document.getElementById('transferAmount')?.value;
  const memo = document.getElementById('transferMemo')?.value || '';
  if (!amount || isNaN(amount) || Number(amount) <= 0) {
    showToast('Please enter a valid amount.', 'warn'); return;
  }
  const details = document.getElementById('reviewDetails');
  if (details) {
    details.innerHTML = `
      <div class="flex justify-between py-2 border-b border-slate-50 text-sm"><span class="text-slate-500">Amount</span><span class="font-semibold text-slate-800">$${Number(amount).toFixed(2)}</span></div>
      <div class="flex justify-between py-2 text-sm"><span class="text-slate-500">Memo</span><span class="font-semibold text-slate-800">${memo || '—'}</span></div>
    `;
  }
  document.getElementById('transferForm')?.classList.add('hidden');
  document.getElementById('transferReview')?.classList.remove('hidden');
}

function backToForm() {
  document.getElementById('transferForm')?.classList.remove('hidden');
  document.getElementById('transferReview')?.classList.add('hidden');
}

async function confirmTransfer() {
  const amount = document.getElementById('transferAmount')?.value;
  const memo = document.getElementById('transferMemo')?.value || '';
  try {
    await fetch(`${API_BASE}/api/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: `Transfer $${amount} — ${memo}`, session_id: sessionId, user_id: currentUser?.id || '' }),
    });
  } catch (e) { /* network error — proceed to success UI anyway */ }
  document.getElementById('transferReview')?.classList.add('hidden');
  document.getElementById('transferSuccess')?.classList.remove('hidden');
  const sd = document.getElementById('successDetails');
  if (sd) sd.innerHTML = `<div class="flex justify-between text-sm"><span class="text-slate-500">Amount</span><span class="font-semibold">$${Number(amount).toFixed(2)}</span></div>`;
  if (currentUser) currentUser.checking = Math.max(0, currentUser.checking - Number(amount));
}

function doQuickTransfer() {
  const amount = parseFloat(document.getElementById('quickAmount')?.value || '0');
  if (!amount || amount <= 0) { showToast('Please enter a valid amount.', 'warn'); return; }
  showToast(`Transfer of $${amount.toFixed(2)} submitted successfully!`, 'success');
  const el = document.getElementById('quickAmount');
  if (el) el.value = '';
}

function renderTransactions() {
  // appShell transaction search — no-op for first HTML system
}
