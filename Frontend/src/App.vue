<script setup></script>

<template>
  <RouterView />
</template>

<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

*, *::before, *::after {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

:root {
  --bg-primary: #0d0f17;
  --bg-secondary: #131620;
  --bg-card: #1a1d2e;
  --bg-input: #1e2235;
  --bg-user: #2563eb;
  --bg-hover: #252840;

  --border: rgba(255, 255, 255, 0.07);
  --border-focus: rgba(99, 102, 241, 0.6);

  --text-primary: #f0f2ff;
  --text-secondary: #8b8fa8;
  --text-muted: #5c6079;

  --accent: #6366f1;
  --accent-light: #818cf8;
  --accent-glow: rgba(99, 102, 241, 0.25);

  --success: #10b981;
  --warning: #f59e0b;
  --danger: #ef4444;

  --radius-sm: 8px;
  --radius-md: 14px;
  --radius-lg: 20px;
  --radius-xl: 28px;

  --shadow-glow: 0 0 30px rgba(99, 102, 241, 0.15);
  --shadow-card: 0 4px 24px rgba(0, 0, 0, 0.4);
  --shadow-message: 0 2px 12px rgba(0, 0, 0, 0.3);

  --transition: 0.2s ease;
}

html, body, #app {
  height: 100%;
  width: 100%;
  font-family: 'Inter', system-ui, sans-serif;
  background: var(--bg-primary);
  color: var(--text-primary);
  font-size: 15px;
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
}

/* Scrollbar */
::-webkit-scrollbar { width: 5px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--bg-hover); border-radius: 99px; }
::-webkit-scrollbar-thumb:hover { background: var(--accent); }
</style>
