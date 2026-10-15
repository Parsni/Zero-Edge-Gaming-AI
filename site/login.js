const form = document.getElementById('authForm');
const notice = document.getElementById('notice');
const forms = document.getElementById('authForms');
const accountView = document.getElementById('accountView');
const adminPanel = document.getElementById('adminPanel');
let mode = 'login';

function setMode(next) {
  mode = next;
  document.getElementById('loginTab').classList.toggle('active', mode === 'login');
  document.getElementById('registerTab').classList.toggle('active', mode === 'register');
  document.getElementById('formTitle').textContent = mode === 'login' ? 'Welcome back' : 'Create your account';
  document.getElementById('formHint').textContent = mode === 'login'
    ? 'Use your username and password to continue.'
    : 'The first registered account becomes the administrator. Passwords must have at least 12 characters.';
  document.getElementById('submitButton').textContent = mode === 'login' ? 'Sign in' : 'Register';
  document.getElementById('password').autocomplete = mode === 'login' ? 'current-password' : 'new-password';
  notice.textContent = '';
}
document.getElementById('loginTab').addEventListener('click', () => setMode('login'));
document.getElementById('registerTab').addEventListener('click', () => setMode('register'));

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  notice.textContent = '';
  const endpoint = mode === 'login' ? '/auth/login' : '/auth/register';
  try {
    const response = await fetch(endpoint, {method:'POST', headers:{'Content-Type':'application/json'}, credentials:'same-origin', body:JSON.stringify({username:document.getElementById('username').value,password:document.getElementById('password').value})});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Could not sign in.');
    window.currentUserId = data.id;
    await showAccount(data.username, data.role);
  } catch (error) { notice.textContent = error.message; }
});

document.getElementById('logoutButton').addEventListener('click', async () => {
  await fetch('/auth/logout', {method:'POST', credentials:'same-origin'});
  location.reload();
});

async function showAccount(username, role) {
  forms.style.display = 'none';
  accountView.style.display = 'block';
  document.getElementById('accountHint').textContent = `${username} · ${role}`;
  if (role === 'admin') { adminPanel.hidden = false; await loadUsers(); }
}

async function loadUsers() {
  const response = await fetch('/admin/users', {credentials:'same-origin'});
  if (!response.ok) return;
  const users = await response.json();
  const table = document.createElement('table');
  table.innerHTML = '<thead><tr><th>User</th><th>Role / state</th><th>Actions</th></tr></thead>';
  const body = document.createElement('tbody');
  users.forEach(user => {
    const row = document.createElement('tr');
    const name = document.createElement('td'); name.textContent = user.username;
    const state = document.createElement('td'); state.textContent = `${user.role} · ${user.active ? 'active' : 'disabled'}`;
    const actions = document.createElement('td');
    if (user.role !== 'admin') actions.append(button(user.active ? 'Disable' : 'Enable', () => updateUser(user.id, {active:!user.active}), user.active));
    if (user.role !== 'admin') actions.append(button('Make admin', () => updateUser(user.id, {role:'admin'})));
    else if (user.id !== window.currentUserId) actions.append(button('Demote', () => updateUser(user.id, {role:'user'}), true));
    if (user.id !== window.currentUserId) actions.append(button('Delete', async () => {if (confirm(`Delete ${user.username}?`)) {await fetch(`/admin/users/${user.id}`, {method:'DELETE'}); await loadUsers();}}, true));
    row.append(name,state,actions); body.append(row);
  });
  table.append(body);
  document.getElementById('usersTable').replaceChildren(table);
}
function button(text, callback, danger=false) { const el=document.createElement('button'); el.textContent=text; if(danger)el.className='danger'; el.addEventListener('click',callback); return el; }
async function updateUser(id, changes) { const r=await fetch(`/admin/users/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(changes)}); if(!r.ok){const d=await r.json();alert(d.detail||'Update failed');} await loadUsers(); }

fetch('/auth/me', {credentials:'same-origin'}).then(r=>r.json()).then(data=>{
  if(data.authenticated){window.currentUserId=data.user.id;showAccount(data.user.username,data.user.role);}
  else if(data.registration_open){setMode('register');}
}).catch(()=>{});
