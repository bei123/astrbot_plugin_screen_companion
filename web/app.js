// 认证相关
let isAuthenticated = false;

// 检查认证状态
async function checkAuth() {
    try {
        const response = await fetch('/auth/info');
        const data = await response.json();
        if (data.success) {
            isAuthenticated = !data.requires_auth;
            if (data.requires_auth && !isAuthenticated) {
                showLoginForm();
            }
        }
    } catch (error) {
        console.error('检查认证状态失败:', error);
    }
}

// 显示登录表单
function showLoginForm() {
    const loginForm = document.createElement('div');
    loginForm.id = 'loginForm';
    loginForm.className = 'login-form';
    loginForm.innerHTML = `
        <div class="login-container">
            <h2>请登录</h2>
            <input type="password" id="loginPassword" placeholder="输入密码" />
            <button id="loginButton">登录</button>
            <p id="loginError" class="error"></p>
        </div>
    `;
    document.body.appendChild(loginForm);
    
    document.getElementById('loginButton').addEventListener('click', async function() {
        const password = document.getElementById('loginPassword').value;
        const errorElement = document.getElementById('loginError');
        
        try {
            const response = await fetch('/auth/login', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ password })
            });
            
            const data = await response.json();
            if (data.success) {
                isAuthenticated = true;
                document.getElementById('loginForm').remove();
                // 重新加载当前内容
                const activeSection = document.querySelector('.section.active');
                if (activeSection.id === 'diaries') {
                    loadDiaries();
                } else if (activeSection.id === 'observations') {
                    loadObservations();
                } else if (activeSection.id === 'memories') {
                    loadMemories();
                }
            } else {
                errorElement.textContent = '密码错误';
            }
        } catch (error) {
            errorElement.textContent = '登录失败: ' + error.message;
        }
    });
}

// 导航切换
const navLinks = document.querySelectorAll('.nav-link');
const sections = document.querySelectorAll('.section');

navLinks.forEach(link => {
    link.addEventListener('click', function(e) {
        e.preventDefault();
        
        // 移除所有导航链接的 active 类
        navLinks.forEach(l => l.classList.remove('active'));
        // 添加当前链接的 active 类
        this.classList.add('active');
        
        // 隐藏所有 sections
        sections.forEach(section => section.classList.remove('active'));
        // 显示对应 section
        const targetId = this.getAttribute('href').substring(1);
        document.getElementById(targetId).classList.add('active');
        
        // 加载对应内容
        if (targetId === 'diaries') {
            loadDiaries();
        } else if (targetId === 'observations') {
            loadObservations();
        } else if (targetId === 'memories') {
            loadMemories();
        }
    });
});

// 加载今日日记
async function loadDiaries() {
    // 获取今天的日期，格式为 YYYY-MM-DD
    const today = new Date().toISOString().split('T')[0];
    // 清空日记列表，只显示今日内容
    const diaryList = document.getElementById('diaryList');
    diaryList.innerHTML = '';
    // 加载今日日记详情
    const diaryDetail = document.getElementById('diaryDetail');
    diaryDetail.innerHTML = '<p>加载中...</p>';
    
    try {
        const response = await fetch(`/api/diary/${today}`);
        const data = await response.json();
        
        if (data.success) {
            diaryDetail.innerHTML = `
                <h3>${data.date} 的日记</h3>
                <pre>${data.content || '今天还没有写日记'}</pre>
            `;
        } else {
            diaryDetail.innerHTML = `<p>加载失败: ${data.error}</p>`;
        }
    } catch (error) {
        diaryDetail.innerHTML = `<p>加载失败: ${error.message}</p>`;
    }
}

// 加载日记详情
async function loadDiaryDetail(date) {
    const diaryDetail = document.getElementById('diaryDetail');
    diaryDetail.innerHTML = '<p>加载中...</p>';
    
    try {
        const response = await fetch(`/api/diary/${date}`);
        const data = await response.json();
        
        if (data.success) {
            diaryDetail.innerHTML = `
                <h3>${data.date} 的日记</h3>
                <pre>${data.content}</pre>
            `;
        } else {
            diaryDetail.innerHTML = `<p>加载失败: ${data.error}</p>`;
        }
    } catch (error) {
        diaryDetail.innerHTML = `<p>加载失败: ${error.message}</p>`;
    }
}

// 加载观察记录
async function loadObservations() {
    const observationList = document.getElementById('observationList');
    observationList.innerHTML = '<p>加载中...</p>';
    
    try {
        const response = await fetch('/api/observations');
        const data = await response.json();
        
        if (data.success) {
            if (data.observations.length === 0) {
                observationList.innerHTML = '<p>没有观察记录</p>';
                return;
            }
            
            observationList.innerHTML = '';
            data.observations.forEach(observation => {
                const observationItem = document.createElement('div');
                observationItem.className = 'observation-item';
                observationItem.innerHTML = `
                    <h4>${observation.timestamp || '未知时间'}</h4>
                    <p>${observation.content || '无内容'}</p>
                `;
                observationList.appendChild(observationItem);
            });
        } else {
            observationList.innerHTML = `<p>加载失败: ${data.error}</p>`;
        }
    } catch (error) {
        observationList.innerHTML = `<p>加载失败: ${error.message}</p>`;
    }
}

// 加载记忆
async function loadMemories() {
    const memoryList = document.getElementById('memoryList');
    memoryList.innerHTML = '<p>加载中...</p>';
    
    try {
        const response = await fetch('/api/memories');
        const data = await response.json();
        
        if (data.success) {
            if (data.memories.length === 0) {
                memoryList.innerHTML = '<p>没有记忆记录</p>';
                return;
            }
            
            memoryList.innerHTML = '';
            data.memories.forEach(memory => {
                const memoryItem = document.createElement('div');
                memoryItem.className = 'memory-item';
                memoryItem.innerHTML = `
                    <h4>${memory.title || '未知标题'}</h4>
                    <p>${memory.content || '无内容'}</p>
                `;
                memoryList.appendChild(memoryItem);
            });
        } else {
            memoryList.innerHTML = `<p>加载失败: ${data.error}</p>`;
        }
    } catch (error) {
        memoryList.innerHTML = `<p>加载失败: ${error.message}</p>`;
    }
}

// 初始加载
window.addEventListener('DOMContentLoaded', async () => {
    await checkAuth();
    loadDiaries();
});