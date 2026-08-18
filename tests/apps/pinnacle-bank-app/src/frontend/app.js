/**
 * Pinnacle Bank â€” SPA Controller
 * Handles: page routing, login, dashboard rendering, AI agent chat
 */

/* â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
   CONFIGURATION
â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â• */
const API_BASE = window.location.origin;

/* â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
   USER DATABASE (demo accounts)
   NOTE: user_id sent with every chat request for agent context.
   Server does not re-validate ownership â€” intentional design.
â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â• */
const USERS = {
  alice: {
    id: 'alice', name: 'Alice Johnson', initials: 'AJ',
    avatarClass: 'bg-gradient-to-br from-violet-400 to-purple-600',
    email: 'alice.johnson@pinnaclebank.com',
    phone: '(â€¢â€¢â€¢) â€¢â€¢â€¢ - 4821',
    checking: 50000.00, savings: 18420.55, investments: 37834.90,
    chkAcct: '****4821', savAcct: '****7293',
  },
  bob: {
    id: 'bob', name: 'Bob Martinez', initials: 'BM',
    avatarClass: 'bg-gradient-to-br from-emerald-400 to-teal-600',
    email: 'bob.martinez@pinnaclebank.com',
    phone: '(â€¢â€¢â€¢) â€¢â€¢â€¢ - 9204',
    checking: 12500.00, savings: 3250.00, investments: 8100.00,
    chkAcct: '****9204', savAcct: '****3311',
  },
  carol: {
    id: 'carol', name: 'Carol Williams', initials: 'CW',
    avatarClass: 'bg-gradient-to-br from-amber-400 to-orange-500',
    email: 'carol.williams@pinnaclebank.com',
    phone: '(â€¢â€¢â€¢) â€¢â€¢â€¢ - 7731',
    checking: 250000.00, savings: 92750.00, investments: 184500.00,
    chkAcct: '****7731', savAcct: '****5509',
  },
};

const EMAIL_MAP = {
  'alice.johnson@pinnaclebank.com': 'alice',
  'bob.martinez@pinnaclebank.com': 'bob',
  'carol.williams@pinnaclebank.com': 'carol',
};

/* â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
   TRANSACTIONS
â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â• */
const TRANSACTIONS = {
  alice: [
    { date:'Apr 10, 2026', merchant:'Meridian Corp Payroll',   category:'Income',       icon:'ðŸ’°', type:'credit', amount:5250.00 },
    { date:'Apr 09, 2026', merchant:'Whole Foods Market',      category:'Groceries',    icon:'ðŸ›’', type:'debit',  amount:127.43 },
    { date:'Apr 08, 2026', merchant:'Netflix',                 category:'Streaming',    icon:'ðŸ“º', type:'debit',  amount:15.99 },
    { date:'Apr 08, 2026', merchant:'Shell Gas Station',       category:'Auto',         icon:'â›½', type:'debit',  amount:68.20 },
    { date:'Apr 07, 2026', merchant:'AT&T Wireless',           category:'Phone',        icon:'ðŸ“±', type:'debit',  amount:89.99 },
    { date:'Apr 06, 2026', merchant:'Starbucks',               category:'Coffee',       icon:'â˜•', type:'debit',  amount:6.45 },
    { date:'Apr 05, 2026', merchant:'Amazon',                  category:'Shopping',     icon:'ðŸ“¦', type:'debit',  amount:234.67 },
    { date:'Apr 04, 2026', merchant:'PSE&G Electric',          category:'Utilities',    icon:'ðŸ’¡', type:'debit',  amount:142.30 },
    { date:'Apr 03, 2026', merchant:'Nobu Restaurant',         category:'Dining',       icon:'ðŸ½ï¸', type:'debit',  amount:189.00 },
    { date:'Apr 02, 2026', merchant:'Dividend Income',         category:'Income',       icon:'ðŸ’°', type:'credit', amount:420.00 },
    { date:'Apr 01, 2026', merchant:'Apple App Store',         category:'Subscriptions',icon:'ðŸ“±', type:'debit',  amount:9.99 },
    { date:'Mar 31, 2026', merchant:'Costco Wholesale',        category:'Groceries',    icon:'ðŸ›’', type:'debit',  amount:312.45 },
    { date:'Mar 29, 2026', merchant:'Allstate Insurance',      category:'Insurance',    icon:'ðŸ›¡ï¸', type:'debit',  amount:287.00 },
    { date:'Mar 28, 2026', merchant:'ATM Withdrawal',          category:'Cash',         icon:'ðŸ§', type:'debit',  amount:200.00 },
    { date:'Mar 27, 2026', merchant:'Venmo Transfer',          category:'Transfer',     icon:'ðŸ’¸', type:'credit', amount:85.00 },
    { date:'Mar 25, 2026', merchant:'Best Buy',                category:'Electronics',  icon:'ðŸ›ï¸', type:'debit',  amount:549.99 },
    { date:'Mar 24, 2026', merchant:'Transfer to Savings',     category:'Transfer',     icon:'ðŸ¦', type:'debit',  amount:500.00 },
  ],
  bob: [
    { date:'Apr 10, 2026', merchant:'Sunrise Bakery Payroll',  category:'Income',       icon:'ðŸ’°', type:'credit', amount:2800.00 },
    { date:'Apr 09, 2026', merchant:"Trader Joe's",            category:'Groceries',    icon:'ðŸ›’', type:'debit',  amount:89.34 },
    { date:'Apr 08, 2026', merchant:'Spotify',                 category:'Streaming',    icon:'ðŸŽµ', type:'debit',  amount:9.99 },
    { date:'Apr 07, 2026', merchant:'BP Gas Station',          category:'Auto',         icon:'â›½', type:'debit',  amount:52.10 },
    { date:'Apr 05, 2026', merchant:"McDonald's",              category:'Dining',       icon:'ðŸ”', type:'debit',  amount:12.35 },
    { date:'Apr 04, 2026', merchant:'Target',                  category:'Shopping',     icon:'ðŸŽ¯', type:'debit',  amount:76.50 },
    { date:'Apr 03, 2026', merchant:'ConEd Electric',          category:'Utilities',    icon:'ðŸ’¡', type:'debit',  amount:98.45 },
    { date:'Apr 01, 2026', merchant:'Planet Fitness',          category:'Fitness',      icon:'ðŸ‹ï¸', type:'debit',  amount:24.99 },
    { date:'Mar 31, 2026', merchant:'Monthly Rent',            category:'Housing',      icon:'ðŸ ', type:'debit',  amount:1450.00 },
  ],
  carol: [
    { date:'Apr 10, 2026', merchant:'Executive Consulting Fee',category:'Income',       icon:'ðŸ’°', type:'credit', amount:22500.00 },
    { date:'Apr 09, 2026', merchant:'Whole Foods Market',      category:'Groceries',    icon:'ðŸ›’', type:'debit',  amount:287.50 },
    { date:'Apr 08, 2026', merchant:'United Airlines',         category:'Travel',       icon:'âœˆï¸', type:'debit',  amount:1240.00 },
    { date:'Apr 07, 2026', merchant:'Four Seasons Hotel',      category:'Travel',       icon:'ðŸ¨', type:'debit',  amount:2100.00 },
    { date:'Apr 05, 2026', merchant:'Investment Dividend',     category:'Income',       icon:'ðŸ’°', type:'credit', amount:3450.00 },
    { date:'Apr 03, 2026', merchant:'Transfer to Investment',  category:'Transfer',     icon:'ðŸ“ˆ', type:'debit',  amount:10000.00 },
  ],
};

/* â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
   CHAT SUGGESTIONS
â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â• */
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

/* â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
   STATE
â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â• */
let currentUser = null;
let selectedUserId = '';
let sessionId = '';
let chatHistory = [];
let isLoading = false;
// VULN-AUTH: JWT stored in localStorage â€” accessible to any JS on the page (XSS risk)
let authToken = localStorage.getItem('pinnacle_access_token') || null;
let refreshToken = localStorage.getItem('pinnacle_refresh_token') || null;
/* DB state */
let dbAccounts     = [];
let dbTransactions = [];
let dbCards        = [];
let dbNotifications= [];
let currentTxFilter = 'all';

/* BANK API HELPER */
async function bankApi(path, options = {}) {
  const headers = {'Content-Type': 'application/json', ...(options.headers || {})};
  if (authToken) headers['Authorization'] = 'Bearer ' + authToken;
  const res = await fetch(API_BASE + path, {...options, headers});
  if (!res.ok) {
    const err = await res.json().catch(() => ({detail: res.statusText}));
    throw new Error(err.detail || 'HTTP ' + res.status);
  }
  return res.json();
}

/* LOAD USER DATA FROM DB */
async function loadUserData() {
  try {
    const results = await Promise.all([
      bankApi('/api/bank/me'),
      bankApi('/api/bank/accounts'),
      bankApi('/api/bank/transactions?limit=100'),
      bankApi('/api/bank/cards'),
      bankApi('/api/bank/notifications'),
    ]);
    const profile = results[0], accounts = results[1], transactions = results[2], cards = results[3], notifications = results[4];
    const AVATAR_MAP = {
      alice: {initials: 'AJ', avatarClass: 'bg-gradient-to-br from-violet-400 to-purple-600'},
      bob:   {initials: 'BM', avatarClass: 'bg-gradient-to-br from-emerald-400 to-teal-600'},
      carol: {initials: 'CW', avatarClass: 'bg-gradient-to-br from-amber-400 to-orange-500'},
    };
    const avatar = AVATAR_MAP[profile.user_id] || {
      initials: profile.name.split(' ').map(function(w){return w[0];}).join('').slice(0,2).toUpperCase(),
      avatarClass: 'bg-gradient-to-br from-blue-400 to-blue-600',
    };
    currentUser = Object.assign({}, profile, avatar, {id: profile.user_id});
    dbAccounts = accounts;
    dbTransactions = transactions;
    dbCards = cards;
    dbNotifications = notifications;
    renderNotifBadge();
    return true;
  } catch(err) {
    console.error('loadUserData failed:', err);
    return false;
  }
}

function renderNotifBadge() {
  var unread = dbNotifications.filter(function(n){return !n.read;}).length;
  var badge = document.getElementById('notifBadge');
  if (badge) { badge.textContent = unread; badge.classList.toggle('hidden', unread === 0); }
  var shellBadge = document.getElementById('shellNotifBadge');
  if (shellBadge) { shellBadge.textContent = unread; shellBadge.classList.toggle('hidden', unread === 0); }
  renderNotifPanel();
  if (typeof renderShellNotifPanel === 'function') renderShellNotifPanel();
}

function renderNotifPanel() {
  const list = document.getElementById('notifList');
  if (!list) return;
  if (!dbNotifications.length) {
    list.innerHTML = '<p class="text-xs text-gray-400 text-center py-4">No notifications</p>';
    return;
  }
  list.innerHTML = dbNotifications.slice(0, 8).map(function(n) {
    return '<div class="px-4 py-3 hover:bg-gray-50 border-b last:border-0' + (n.read ? ' opacity-60' : '') + '">'
      + '<p class="text-sm font-semibold text-gray-800">' + escHtml(n.title) + '</p>'
      + '<p class="text-xs text-gray-500 mt-0.5">' + escHtml(n.body) + '</p>'
      + '</div>';
  }).join('');
}

function fmt(n) {
  return '$' + Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/* PAGE ROUTING */
function navigate(page) {
  const SHELL_PAGES = ['dashboard','accounts','transactions','transfers','profile'];
  const loginView = document.getElementById('loginView');
  const appShell  = document.getElementById('appShell');

  if (SHELL_PAGES.includes(page)) {
    // Show the appShell, hide the login view
    if (loginView) loginView.classList.remove('active');
    if (appShell)  appShell.classList.add('active');
    // Switch the inner page-view section
    document.querySelectorAll('.page-view').forEach(function(p){p.classList.remove('active');});
    var inner = document.getElementById('page-' + page);
    if (inner) inner.classList.add('active');
    // Highlight the sidebar nav item
    document.querySelectorAll('.nav-item').forEach(function(n){n.classList.remove('active');});
    var navItem = document.getElementById('nav-' + page);
    if (navItem) navItem.classList.add('active');
    // Hide old .page elements
    document.querySelectorAll('.page').forEach(function(p){p.classList.remove('active');});
    // Reset transfer form if going anywhere other than transfers
    if (page !== 'transfers') {
      document.getElementById('transferForm')?.classList.remove('hidden');
      document.getElementById('transferReview')?.classList.add('hidden');
      document.getElementById('transferSuccess')?.classList.add('hidden');
    }
  } else if (page === 'login') {
    // Show login view, hide appShell
    if (appShell)  appShell.classList.remove('active');
    if (loginView) loginView.classList.add('active');
    document.querySelectorAll('.page').forEach(function(p){p.classList.remove('active');});
    window.scrollTo(0, loginView ? loginView.offsetTop : 0);
    return;
  } else {
    // Landing page or chat — old .page system
    if (loginView) loginView.classList.remove('active');
    if (appShell)  appShell.classList.remove('active');
    document.querySelectorAll('.page').forEach(function(p){p.classList.remove('active');});
    var target = document.getElementById('page-' + page);
    if (target) target.classList.add('active');
    // landing page: always hide loginView and appShell
  }
  window.scrollTo(0, 0);
}

function logout() {
  currentUser = null;
  dbAccounts = []; dbTransactions = []; dbCards = []; dbNotifications = [];
  authToken = null;
  refreshToken = null;
  sessionId = '';
  chatHistory = [];
  localStorage.removeItem('pinnacle_access_token');
  localStorage.removeItem('pinnacle_refresh_token');
  var chatRoot3 = document.getElementById('chatRoot');
  if (chatRoot3) chatRoot3.classList.add('hidden');
  navigate('login');
}

function initLandingNav() {
  var header = document.getElementById('siteHeader');
  if (!header) return;
  window.addEventListener('scroll', function() {
    if (window.scrollY > 40) {
      header.classList.add('shadow-lg');
    } else {
      header.classList.remove('shadow-lg');
    }
  });
}


/* â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
   DASHBOARD
â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â• */
function renderDashboard() {
  if (!currentUser) { navigate('login'); return; }
  var u = currentUser;
  var setEl = function(id, val) { var el = document.getElementById(id); if (el) el.textContent = val; };

  var chk = dbAccounts.find(function(a){return a.account_type==='checking';}) || {};
  var sav = dbAccounts.find(function(a){return a.account_type==='savings';})  || {};
  var inv = dbAccounts.find(function(a){return a.account_type==='investment';})|| {};

  // Sidebar
  var savEl = document.getElementById('sidebarAvatar');
  if (savEl) { savEl.textContent = u.initials||''; savEl.className = 'w-10 h-10 rounded-full flex items-center justify-center text-white font-semibold text-sm flex-shrink-0 '+(u.avatarClass||''); }
  setEl('sidebarName', u.name || '');
  setEl('sidebarAcct', chk.account_num ? 'Checking '+chk.account_num : '');

  // Header
  var hr = new Date().getHours();
  var greet = hr<12 ? 'Good Morning' : hr<17 ? 'Good Afternoon' : 'Good Evening';
  var firstName = (u.name||'').split(' ')[0];
  setEl('headerGreeting', greet+', '+firstName+' 👋');
  setEl('headerDate', new Date().toLocaleDateString('en-US', {weekday:'long',month:'long',day:'numeric'}));
  var hav = document.getElementById('headerAvatar');
  if (hav) { hav.textContent=u.initials||''; hav.className='w-9 h-9 rounded-full flex items-center justify-center text-white text-sm font-semibold cursor-pointer '+(u.avatarClass||''); }

  // Hero balance
  var total = (chk.balance||0)+(sav.balance||0)+(inv.balance||0);
  setEl('heroBalance', fmt(total));
  setEl('heroAccount', chk.account_num ? 'Checking '+chk.account_num : '');

  // Stats
  var now2 = new Date();
  var thisMonth = dbTransactions.filter(function(tx){ var d=new Date(tx.date); return d.getMonth()===now2.getMonth()&&d.getFullYear()===now2.getFullYear(); });
  var income   = thisMonth.filter(function(t){return t.tx_type==='credit';}).reduce(function(s,t){return s+t.amount;},0);
  var expenses = thisMonth.filter(function(t){return t.tx_type==='debit';}).reduce(function(s,t){return s+t.amount;},0);
  var savRate  = income>0 ? Math.round(((income-expenses)/income)*100) : 0;
  setEl('statIncome',   fmt(income));
  setEl('statExpenses', fmt(expenses));
  setEl('statSavings',  savRate+'%');

  // Quick transfer
  var qf = document.getElementById('quickFromAcct');
  if (qf) qf.textContent = chk.account_num ? 'Checking '+chk.account_num+' · '+fmt(chk.balance||0) : '';

  // Accounts page
  setEl('acctCheckingBalance', fmt(chk.balance||0));
  setEl('acctCheckingNum',     chk.account_num||'');
  setEl('acctFullNum',         chk.account_num ? '••••'+chk.account_num.replace('****','') : '');
  setEl('acctSavingsBalance',  fmt(sav.balance||0));
  var last4 = chk.account_num ? chk.account_num.replace('****','') : '0000';
  setEl('cardNumber', '•••• •••• •••• '+last4);
  setEl('cardHolder', (u.name||'').toUpperCase());
  var cardAction = document.getElementById('cardSettingsBtn');
  if (cardAction) cardAction.textContent = dbCards[0] && dbCards[0].frozen ? 'Unfreeze Card' : 'Freeze Card';

  // Profile page
  var pav = document.getElementById('profileAvatar');
  if (pav) { pav.textContent=u.initials||''; pav.className='w-20 h-20 rounded-full flex items-center justify-center text-white text-2xl font-bold mx-auto mb-4 '+(u.avatarClass||''); }
  setEl('profileName',  u.name||'');
  setEl('profileEmail', u.email||'');
  setEl('profilePhone', u.phone||'');

  _populateTransferSelects();
  renderTxList('recentTxList', dbTransactions, 5);
  renderTransactions();
}

function _txIcon(cat) {
  const m = { Income:'💰', Groceries:'🛒', Streaming:'📺', Auto:'⛽', Phone:'📱', Coffee:'☕', Shopping:'📦', Utilities:'💡', Dining:'🍽️', Insurance:'🛡️', Cash:'🏧', Transfer:'💸', Electronics:'🛍️', Travel:'✈️', Housing:'🏠', Fitness:'🏋️', Subscriptions:'📱' };
  return m[cat] || '💳';
}

function renderTxList(containerId, txs, limit, filterType) {
  const container = document.getElementById(containerId);
  if (!container) return;
  let list = filterType ? txs.filter(t => t.tx_type === filterType) : txs;
  const slice = list.slice(0, limit);
  if (!slice.length) { container.innerHTML = '<p class="text-sm text-gray-400 text-center py-6">No transactions.</p>'; return; }
  container.innerHTML = slice.map(tx => `
    <div class="tx-row">
      <div class="w-10 h-10 rounded-full bg-gray-50 border border-gray-100 flex items-center justify-center text-lg flex-shrink-0 mr-4">${_txIcon(tx.category)}</div>
      <div class="flex-1 min-w-0">
        <div class="text-sm font-semibold text-gray-800 truncate">${escHtml(tx.merchant)}</div>
        <div class="text-xs text-gray-400">${escHtml(tx.category)} &middot; ${escHtml(tx.date)}</div>
      </div>
      <div class="text-sm font-bold ml-4 ${tx.tx_type === 'credit' ? 'tx-credit' : 'tx-debit'}">
        ${tx.tx_type === 'credit' ? '+' : '-'}${fmt(tx.amount)}
      </div>
    </div>
  `).join('');
}

/* â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
   DASHBOARD TABS
â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â• */
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

/* â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
   CHAT INIT
â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â• */
function initChat() {
  if (!currentUser) { navigate('login'); return; }

  // Render suggestion list in sidebar â€” use createElement to avoid JSON/quote escaping issues
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
    messages.innerHTML = '';
    appendNovaMessage(`Hi ${currentUser.name.split(' ')[0]}! I'm **Nova**, your Pinnacle Bank AI assistant.

I can help you check balances, review transactions, get market updates, assist with transfers, and much more. How can I help you today?`, 'Nova');
  }
}

/* â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
   CHAT SEND / RECEIVE
â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â• */
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
  const sendButton = document.getElementById('chatSendBtn');
  if (!input) return;
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
  if (sendButton) sendButton.disabled = true;
  const typingId = showTyping();

  try {
    const payload = {
      message: text,
      session_id: sessionId,
      user_id: currentUser ? currentUser.id : '',
      auth_key: authToken || '',
    };

    // VULN-AUTH-03: JWT sent on every request â€” token payload contains balances,
    // kyc_level, risk_score. Any network observer or XSS payload reading
    // localStorage can decode it without the signing secret.
    const headers = { 'Content-Type': 'application/json' };
    if (authToken) headers['Authorization'] = `Bearer ${authToken}`;

    const res = await fetch(`${API_BASE}/api/chat`, {
      method: 'POST',
      headers,
      body: JSON.stringify(payload),
    });

    removeTyping(typingId);

    if (!res.ok) {
      let errorDetail = {};
      try {
        errorDetail = await res.json();
      } catch (_) {
        errorDetail = {};
      }
      const detail = errorDetail.detail || {};
      const retryAfter = res.headers.get('Retry-After') || detail.retry_after;
      const err = new Error(detail.message || `HTTP ${res.status}`);
      err.status = res.status;
      err.errorType = detail.error || res.headers.get('X-LLM-Error-Type') || '';
      err.retryAfter = retryAfter;
      throw err;
    }
    const data = await res.json();
    const reply = data.response || 'Sorry, I did not receive a valid response.';
    const agentType = data.agent_type || 'Nova';
    appendNovaMessage(reply, agentType);
    chatHistory.push({ role: 'assistant', content: reply });

  } catch (err) {
    removeTyping(typingId);
    if (err.status === 429 || err.errorType === 'llm_rate_limited') {
      const wait = err.retryAfter ? ` Please try again in about ${err.retryAfter} seconds.` : ' Please try again shortly.';
      appendNovaMessage(`Nova is temporarily busy.${wait}`, 'Nova');
    } else if (err.status >= 500 && err.errorType) {
      appendNovaMessage("Nova's AI service is unavailable right now. Please try again in a moment.", 'Nova');
    } else {
      appendNovaMessage("I'm having trouble connecting right now. Please try again in a moment.", 'Nova');
    }
    console.error('Chat error:', err);
  } finally {
    isLoading = false;
    if (sendButton) sendButton.disabled = false;
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
    'Nova':               { color: 'from-brand-500 to-violet-600', label: 'Nova',               icon: 'ðŸ¦' },
    'FraudGuard':         { color: 'from-red-500 to-rose-600',     label: 'FraudGuard',         icon: 'ðŸ›¡ï¸' },
    'CreditAdvisor':      { color: 'from-amber-500 to-orange-600', label: 'CreditAdvisor',      icon: 'ðŸ“Š' },
    'ComplianceOfficer':  { color: 'from-teal-500 to-cyan-600',    label: 'ComplianceOfficer',  icon: 'âš–ï¸' },
    'WealthManager':      { color: 'from-emerald-500 to-green-600',label: 'WealthManager',      icon: 'ðŸ’¹' },
    'RiskAnalyst':        { color: 'from-purple-500 to-indigo-600',label: 'RiskAnalyst',        icon: 'ðŸ”' },
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
      <span class="text-xs text-gray-400 font-medium ml-1">${meta.label}</span>
      <div class="bubble-nova px-4 py-3 text-sm leading-relaxed max-w-lg">${renderMarkdown(text)}</div>
    </div>
  `;
  msgs.appendChild(div);
  msgs.scrollTop = msgs.scrollHeight;
}

function escapeHtmlRaw(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function renderMarkdownInline(value) {
  const codeSpans = [];
  let text = value.replace(/`([^`]+)`/g, function(_, code) {
    const token = `@@INLINE_CODE_${codeSpans.length}@@`;
    codeSpans.push(`<code class="bg-slate-100 border border-slate-200 rounded px-1 py-0.5 text-[0.85em] font-mono text-slate-800">${code}</code>`);
    return token;
  });

  text = text.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+|mailto:[^\s)]+)\)/g, function(_, label, href) {
    return `<a href="${href}" target="_blank" rel="noopener noreferrer" class="text-brand-600 hover:text-brand-700 underline underline-offset-2">${label}</a>`;
  });
  text = text.replace(/\*\*([^*]+)\*\*/g, '<strong class="font-semibold text-slate-900">$1</strong>');
  text = text.replace(/__([^_]+)__/g, '<strong class="font-semibold text-slate-900">$1</strong>');
  text = text.replace(/(^|\s)\*([^*]+)\*/g, '$1<em>$2</em>');
  text = text.replace(/(^|\s)_([^_]+)_/g, '$1<em>$2</em>');

  codeSpans.forEach(function(html, index) {
    text = text.replace(`@@INLINE_CODE_${index}@@`, html);
  });
  return text;
}

function renderMarkdown(markdown) {
  const source = escapeHtmlRaw(markdown).replace(/\r\n/g, '\n').trim();
  if (!source) return '';

  const codeBlocks = [];
  const withCodeTokens = source.replace(/```([a-zA-Z0-9_-]+)?\n?([\s\S]*?)```/g, function(_, lang, code) {
    const token = `@@CODE_BLOCK_${codeBlocks.length}@@`;
    const label = lang ? `<div class="text-[11px] uppercase tracking-wide text-slate-400 mb-2">${lang}</div>` : '';
    codeBlocks.push(`<pre class="my-3 overflow-x-auto rounded-xl bg-slate-950 px-4 py-3 text-xs leading-relaxed text-slate-100">${label}<code>${code}</code></pre>`);
    return token;
  });

  const lines = withCodeTokens.split('\n');
  const out = [];
  let listType = null;

  function closeList() {
    if (!listType) return;
    out.push(listType === 'ol' ? '</ol>' : '</ul>');
    listType = null;
  }

  lines.forEach(function(line) {
    const trimmed = line.trim();
    if (!trimmed) {
      closeList();
      return;
    }

    const codeMatch = trimmed.match(/^@@CODE_BLOCK_(\d+)@@$/);
    if (codeMatch) {
      closeList();
      out.push(codeBlocks[Number(codeMatch[1])]);
      return;
    }

    const heading = trimmed.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      closeList();
      const level = Math.min(heading[1].length, 3);
      const size = level === 1 ? 'text-base' : 'text-sm';
      out.push(`<h${level} class="mt-3 first:mt-0 mb-1 font-semibold ${size} text-slate-900">${renderMarkdownInline(heading[2])}</h${level}>`);
      return;
    }

    const unordered = trimmed.match(/^[-*]\s+(.+)$/);
    if (unordered) {
      if (listType !== 'ul') {
        closeList();
        listType = 'ul';
        out.push('<ul class="my-2 list-disc space-y-1 pl-5">');
      }
      out.push(`<li>${renderMarkdownInline(unordered[1])}</li>`);
      return;
    }

    const ordered = trimmed.match(/^\d+\.\s+(.+)$/);
    if (ordered) {
      if (listType !== 'ol') {
        closeList();
        listType = 'ol';
        out.push('<ol class="my-2 list-decimal space-y-1 pl-5">');
      }
      out.push(`<li>${renderMarkdownInline(ordered[1])}</li>`);
      return;
    }

    closeList();
    if (trimmed.startsWith('&gt; ')) {
      out.push(`<blockquote class="my-2 border-l-4 border-brand-200 pl-3 text-slate-600">${renderMarkdownInline(trimmed.slice(5))}</blockquote>`);
    } else {
      out.push(`<p class="my-2 first:mt-0 last:mb-0">${renderMarkdownInline(trimmed)}</p>`);
    }
  });
  closeList();
  return out.join('');
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

/* â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
   TOKEN REFRESH (VULN-AUTH-06: refresh token never expires)
â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â• */
async function tryRefreshToken() {
  if (!refreshToken) return false;
  try {
    const res = await fetch(`${API_BASE}/api/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!res.ok) return false;
    const data = await res.json();
    authToken = data.access_token;
    localStorage.setItem('pinnacle_access_token', authToken);
    return true;
  } catch {
    return false;
  }
}

/* â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
   INIT
â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â• */
document.addEventListener('DOMContentLoaded', () => {
  navigate('landing');
  initLandingNav();
});

/* â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
   TOAST UTILITY
â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â• */
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

/* â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
   PROFILE DROPDOWN
â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â• */
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

/* â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
   NOTIFICATION PANEL
â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â• */
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

/* â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
   CARD ACTIONS
â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â• */
async function toggleFreezeCard() {
  const card = dbCards[0];
  if (!card) { showToast('No card found.', 'error'); return; }
  try {
    const r = await bankApi(`/api/bank/cards/${card.card_id}/freeze`, { method: 'PATCH', body: JSON.stringify({ frozen: !card.frozen }) });
    card.frozen = r.frozen;
    const btn = document.getElementById('freezeCardBtn');
    if (btn) {
      btn.textContent = card.frozen ? 'Unfreeze Card' : 'Freeze Card';
      btn.classList.toggle('bg-red-50', card.frozen);
      btn.classList.toggle('border-red-200', card.frozen);
      btn.classList.toggle('text-red-600', card.frozen);
    }
    showToast(card.frozen ? 'Card frozen.' : 'Card unfrozen successfully.', card.frozen ? 'warn' : 'success');
    dbNotifications = await bankApi('/api/bank/notifications');
    renderNotifBadge();
  } catch (err) { showToast(err.message || 'Failed to update card.', 'error'); }
}

/* â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
   INTERNAL TRANSFER (section-transfers form)
â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â• */
async function doInternalTransfer() {
  const amountEl = document.getElementById('internalAmount');
  const amount = parseFloat(amountEl ? amountEl.value : 0);
  if (!amount || amount <= 0) { showToast('Please enter a valid transfer amount.', 'warn'); return; }
  const fromAcct = dbAccounts.find(a => a.account_type === 'checking');
  const toAcct   = dbAccounts.find(a => a.account_type === 'savings');
  if (!fromAcct || !toAcct) { showToast('Account data not loaded.', 'error'); return; }
  try {
    const r = await bankApi('/api/bank/transfer/internal', { method: 'POST', body: JSON.stringify({ from_account_id: fromAcct.account_id, to_account_id: toAcct.account_id, amount, memo: 'Internal transfer' }) });
    fromAcct.balance = r.from_balance; toAcct.balance = r.to_balance;
    if (amountEl) amountEl.value = '';
    showToast(`$${amount.toFixed(2)} transferred to savings!`, 'success');
    renderDashboard();
  } catch (err) { showToast(err.message || 'Transfer failed.', 'error'); }
}

async function doZelleTransfer() {
  const amountEl = document.getElementById('zelleAmount');
  const recipientEl = document.getElementById('zelleRecipient');
  const amount = parseFloat(amountEl ? amountEl.value : 0);
  const recipient = recipientEl ? recipientEl.value.trim() : '';
  if (!amount || amount <= 0) { showToast('Please enter a valid amount.', 'warn'); return; }
  if (!recipient) { showToast('Please enter a recipient email or phone.', 'warn'); return; }
  const fromAcct = dbAccounts.find(a => a.account_type === 'checking');
  if (!fromAcct) { showToast('Account data not loaded.', 'error'); return; }
  try {
    const r = await bankApi('/api/bank/transfer/external', { method: 'POST', body: JSON.stringify({ from_account_id: fromAcct.account_id, amount, recipient_email: recipient, memo: 'Zelle payment' }) });
    fromAcct.balance = r.new_balance;
    if (amountEl) amountEl.value = '';
    if (recipientEl) recipientEl.value = '';
    showToast(`$${amount.toFixed(2)} sent to ${recipient} via Zelle®`, 'success');
    renderDashboard();
  } catch (err) { showToast(err.message || 'Transfer failed.', 'error'); }
}

/* â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
   ALIASES & STUBS (for second HTML appShell section)
â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â• */
const signOut = logout;
const sendSuggestion = useSuggestion;
const sendChatMessage = sendChat;

function toggleShellNotif() {
  var panel = document.getElementById('shellNotifPanel');
  if (!panel) return;
  panel.classList.toggle('hidden');
  renderShellNotifPanel();
  if (!panel.classList.contains('hidden')) {
    setTimeout(function() {
      document.addEventListener('click', function outsideSN(e) {
        var btn = document.getElementById('shellNotifBtn');
        if (!panel.contains(e.target) && e.target !== btn && !(btn && btn.contains(e.target))) {
          panel.classList.add('hidden');
          document.removeEventListener('click', outsideSN);
        }
      });
    }, 10);
  }
}

function renderShellNotifPanel() {
  var list = document.getElementById('shellNotifList');
  if (!list) return;
  if (!dbNotifications.length) {
    list.innerHTML = '<p class="text-xs text-slate-400 text-center py-5">No notifications</p>';
    return;
  }
  list.innerHTML = dbNotifications.slice(0, 10).map(function(n) {
    return '<div class="px-4 py-3 hover:bg-slate-50 transition-colors' + (n.read ? ' opacity-60' : '') + '">'
      + '<p class="text-sm font-semibold text-slate-800">' + escHtml(n.title) + '</p>'
      + '<p class="text-xs text-slate-400 mt-0.5">' + escHtml(n.body) + '</p>'
      + '</div>';
  }).join('');
}

function toggleChat() {
  const root = document.getElementById('chatRoot');
  if (root) root.classList.remove('hidden');
  const panel = document.getElementById('chatPanel');
  if (panel) panel.classList.toggle('open');
}

function selectUser(userId, el) {
  document.querySelectorAll('.user-card').forEach(c => c.classList.remove('selected'));
  if (el) el.classList.add('selected');
  selectedUserId = userId;
  const u = USERS[userId];
  if (!u) return;
  const pinSection = document.getElementById('pinSection');
  if (pinSection) { pinSection.classList.remove('hidden'); }
  const nameEl = document.getElementById('pinUserName');
  if (nameEl) nameEl.textContent = u.name;
  const pinInput = document.getElementById('pinInput');
  if (pinInput) { pinInput.value = ''; pinInput.focus(); }
}

async function doLogin() {
  const pinInput = document.getElementById('pinInput');
  const password = pinInput ? pinInput.value.trim() : '';
  if (!password) return;
  if (!selectedUserId) { showLoginError('Please select an account first.'); return; }

  pinInput.disabled = true;
  const btn = document.querySelector('#pinSection button');
  if (btn) btn.textContent = 'Signing in…';

  try {
    const res = await fetch(API_BASE + '/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ auth_key: password, user_id: selectedUserId }),
    });

    if (!res.ok) {
      showLoginError('Invalid key. Try: demo123');
      return;
    }

    const data = await res.json();
    authToken = data.access_token;
    refreshToken = data.refresh_token;
    localStorage.setItem('pinnacle_access_token', authToken);
    localStorage.setItem('pinnacle_refresh_token', refreshToken);
    sessionId = 'sess_' + Math.random().toString(36).slice(2);

    const ok = await loadUserData();
    if (!ok) { showLoginError('Failed to load account data.'); return; }

    // Show chat FAB
    const chatRoot = document.getElementById('chatRoot');
    if (chatRoot) chatRoot.classList.remove('hidden');

    navigate('dashboard');
    renderDashboard();
  } catch (err) {
    showLoginError('Connection error. Please try again.');
    console.error('doLogin error:', err);
  } finally {
    if (pinInput) pinInput.disabled = false;
    if (btn) btn.textContent = 'Sign In';
  }
}

function showLoginError(msg) {
  const existing = document.getElementById('loginViewError');
  if (existing) { existing.textContent = msg; existing.classList.remove('hidden'); return; }
  const pinSection = document.getElementById('pinSection');
  if (!pinSection) return;
  const err = document.createElement('p');
  err.id = 'loginViewError';
  err.className = 'text-red-400 text-sm text-center mt-2';
  err.textContent = msg;
  pinSection.appendChild(err);
}

async function attemptLogin() {
  var email = (document.getElementById('loginEmail')?.value || '').trim().toLowerCase();
  var password = document.getElementById('loginPassword')?.value || '';
  var errorDiv = document.getElementById('loginError');
  var errorMsg = document.getElementById('loginErrorMsg');
  try {
    const res = await fetch(`${API_BASE}/api/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ auth_key: password, user_id: EMAIL_MAP[email] || '' }),
    });
    if (!res.ok) {
      if (errorDiv) errorDiv.classList.remove('hidden');
      if (errorMsg) errorMsg.textContent = 'Invalid email or password. Try a demo account below.';
      return;
    }
    const data = await res.json();
    authToken = data.access_token;
    refreshToken = data.refresh_token;
    localStorage.setItem('pinnacle_access_token', authToken);
    localStorage.setItem('pinnacle_refresh_token', refreshToken);
    if (errorDiv) errorDiv.classList.add('hidden');
    sessionId = 'sess_' + Math.random().toString(36).slice(2);
    const ok2 = await loadUserData();
    if (!ok2) {
      if (errorDiv) errorDiv.classList.remove('hidden');
      if (errorMsg) errorMsg.textContent = 'Failed to load account data.';
      return;
    }
    // Show chat FAB
    var chatRoot2 = document.getElementById('chatRoot');
    if (chatRoot2) chatRoot2.classList.remove('hidden');
    navigate('dashboard');
    renderDashboard();
  } catch (err) {
    if (errorDiv) errorDiv.classList.remove('hidden');
    if (errorMsg) errorMsg.textContent = 'Login failed. Please try again.';
    console.error('Login error:', err);
  }
}

function fillLogin(email, password) {
  var emailEl = document.getElementById('loginEmail');
  var passEl = document.getElementById('loginPassword');
  if (emailEl) emailEl.value = email;
  if (passEl) passEl.value = password;
  attemptLogin();
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
  currentTxFilter = type;
  ['all', 'credit', 'debit'].forEach(t => {
    const btn = document.getElementById('filter-' + t);
    if (!btn) return;
    btn.className = t === type ? 'px-4 py-2 text-sm font-medium bg-navy-800 text-white' : 'px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50';
  });
  renderTransactions();
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
      <div class="flex justify-between py-2 text-sm"><span class="text-slate-500">Memo</span><span class="font-semibold text-slate-800">${escHtml(memo) || '&mdash;'}</span></div>
    `;
  }
  document.getElementById('transferForm')?.classList.add('hidden');
  document.getElementById('transferReview')?.classList.remove('hidden');
}

function backToForm() {
  document.getElementById('transferForm')?.classList.remove('hidden');
  document.getElementById('transferReview')?.classList.add('hidden');
}

function _populateTransferSelects() {
  const toSel = document.getElementById('transferToInternal');
  if (toSel && dbAccounts.length > 1) {
    const fromAcct = dbAccounts[0];
    const fromDiv = document.getElementById('transferFrom');
    if (fromDiv) fromDiv.textContent = fromAcct.account_type.charAt(0).toUpperCase() + fromAcct.account_type.slice(1) + ' ' + fromAcct.account_num;
    toSel.innerHTML = dbAccounts.filter(function(a){return a.account_id !== fromAcct.account_id;}).map(function(a){
      return '<option value="' + a.account_id + '">' + a.account_type.charAt(0).toUpperCase() + a.account_type.slice(1) + ' ' + a.account_num + '</option>';
    }).join('');
  }
  const quickSel = document.getElementById('quickToAcct');
  if (quickSel && dbAccounts.length) {
    quickSel.innerHTML = '<option value="">Select recipient…</option>' + dbAccounts.map(function(a){
      return '<option value="' + a.account_id + '">' + a.account_type.charAt(0).toUpperCase() + a.account_type.slice(1) + ' ' + a.account_num + '</option>';
    }).join('');
  }
}

async function confirmTransfer() {
  const amount = parseFloat(document.getElementById('transferAmount')?.value || '0');
  const memo   = document.getElementById('transferMemo')?.value || '';
  const toId   = document.getElementById('transferToInternal')?.value;
  const fromAcct = dbAccounts[0];
  if (!fromAcct || !toId || !amount) { showToast('Missing transfer details.', 'warn'); return; }
  try {
    const r = await bankApi('/api/bank/transfer/internal', {method: 'POST', body: JSON.stringify({from_account_id: fromAcct.account_id, to_account_id: toId, amount, memo})});
    const fa = dbAccounts.find(function(a){return a.account_id === fromAcct.account_id;});
    const ta = dbAccounts.find(function(a){return a.account_id === toId;});
    if (fa) fa.balance = r.from_balance;
    if (ta) ta.balance = r.to_balance;
    await loadUserData();
  } catch(err) { showToast(err.message || 'Transfer failed.', 'error'); return; }
  document.getElementById('transferReview')?.classList.add('hidden');
  document.getElementById('transferSuccess')?.classList.remove('hidden');
  const sd = document.getElementById('successDetails');
  if (sd) sd.innerHTML = '<div class="flex justify-between text-sm"><span class="text-slate-500">Amount</span><span class="font-semibold">$' + amount.toFixed(2) + '</span></div>';
  renderDashboard();
}

async function doQuickTransfer() {
  const amount = parseFloat(document.getElementById('quickAmount')?.value || '0');
  const toId   = document.getElementById('quickToAcct')?.value;
  if (!amount || amount <= 0) { showToast('Please enter a valid amount.', 'warn'); return; }
  const fromAcct = dbAccounts.find(function(a){return a.account_type === 'checking';});
  if (!fromAcct || !toId) { showToast('Select a destination account.', 'warn'); return; }
  try {
    const r = await bankApi('/api/bank/transfer/internal', {method: 'POST', body: JSON.stringify({from_account_id: fromAcct.account_id, to_account_id: toId, amount, memo: 'Quick transfer'})});
    const fa = dbAccounts.find(function(a){return a.account_id === fromAcct.account_id;});
    const ta = dbAccounts.find(function(a){return a.account_id === toId;});
    if (fa) fa.balance = r.from_balance;
    if (ta) ta.balance = r.to_balance;
    await loadUserData();
    const el = document.getElementById('quickAmount');
    if (el) el.value = '';
    showToast('$' + amount.toFixed(2) + ' transferred successfully!', 'success');
    renderDashboard();
  } catch(err) { showToast(err.message || 'Transfer failed.', 'error'); }
}

function renderTransactions() {
  var qEl = document.getElementById('txSearch');
  var q = qEl ? qEl.value.toLowerCase().trim() : '';
  var txs = dbTransactions;
  if (currentTxFilter && currentTxFilter !== 'all') txs = txs.filter(function(t){ return t.tx_type === currentTxFilter; });
  if (q) txs = txs.filter(function(t){ return (t.merchant||'').toLowerCase().indexOf(q)>=0||(t.category||'').toLowerCase().indexOf(q)>=0; });
  var body = document.getElementById('txBody');
  if (!body) return;
  if (!txs.length) { body.innerHTML = '<p class="text-sm text-slate-400 text-center py-8">No transactions found.</p>'; return; }
  body.innerHTML = txs.map(function(tx) {
    var color = tx.tx_type==='credit' ? 'text-emerald-600' : 'text-slate-800';
    var sign  = tx.tx_type==='credit' ? '+' : '-';
    return '<div class="grid grid-cols-12 px-5 py-3.5 hover:bg-slate-50 transition-colors">'
      +'<div class="col-span-5 flex items-center gap-3"><div class="w-8 h-8 bg-slate-100 rounded-lg flex items-center justify-center text-base">'+_txIcon(tx.category)+'</div>'
      +'<span class="text-slate-700 text-sm font-medium truncate">'+escHtml(tx.merchant)+'</span></div>'
      +'<div class="col-span-3 flex items-center"><span class="text-slate-500 text-sm">'+escHtml(tx.category)+'</span></div>'
      +'<div class="col-span-2 flex items-center"><span class="text-slate-500 text-sm">'+escHtml(tx.date)+'</span></div>'
      +'<div class="col-span-2 flex items-center justify-end"><span class="text-sm font-semibold '+color+'">'+sign+fmt(tx.amount)+'</span></div>'
      +'</div>';
  }).join('');
}

function viewStatements() {
  navigate('transactions');
  currentTxFilter = 'all';
  var search = document.getElementById('txSearch');
  if (search) search.value = '';
  filterTx('all');
  showToast('Showing statement activity.', 'success');
}

function openCardSettings() {
  toggleFreezeCard();
}

function transferIntoSavings() {
  navigate('transfers');
  var savings = dbAccounts.find(function(a){return a.account_type === 'savings';});
  var toSel = document.getElementById('transferToInternal');
  if (toSel && savings) toSel.value = savings.account_id;
  var amount = document.getElementById('transferAmount');
  if (amount) amount.focus();
  showToast('Savings transfer ready.', 'success');
}

function setSavingsGoal() {
  var target = window.prompt('Savings goal amount', '25000');
  if (target === null) return;
  var amount = Number(target.replace(/[^0-9.]/g, ''));
  if (!amount || amount <= 0) { showToast('Enter a valid savings goal.', 'warn'); return; }
  var savings = dbAccounts.find(function(a){return a.account_type === 'savings';}) || {};
  var progress = Math.min(100, Math.round(((savings.balance || 0) / amount) * 100));
  var status = document.getElementById('savingsGoalStatus');
  if (status) status.textContent = 'Goal: ' + fmt(amount) + ' · ' + progress + '% funded';
  showToast('Savings goal updated.', 'success');
}


/* account management via Nova AI chat */

async function markAllNotifsRead() {
  try {
    await bankApi('/api/bank/notifications/read-all', { method: 'POST' });
    dbNotifications = dbNotifications.map(function(n){return Object.assign({},n,{read:true});});
    renderNotifBadge();
    var p1 = document.getElementById('notifPanel'); if (p1) p1.classList.add('hidden');
    var p2 = document.getElementById('shellNotifPanel'); if (p2) p2.classList.add('hidden');
    showToast('All notifications marked as read.', 'success');
  } catch (err) { showToast('Failed to mark notifications.', 'error'); }
}

async function saveSettings() {
  const name  = document.getElementById('settingsName')?.value.trim();
  const phone = document.getElementById('settingsPhone')?.value.trim();
  try {
    await bankApi('/api/bank/me', { method: 'PATCH', body: JSON.stringify({ name, phone }) });
    if (currentUser) { currentUser.name = name; currentUser.phone = phone; }
    showToast('Settings saved.', 'success');
    renderDashboard();
  } catch (err) { showToast(err.message || 'Save failed.', 'error'); }
}
