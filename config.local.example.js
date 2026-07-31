/* Скопируйте в config.local.js — этот файл не коммитится в git.
   Supabase: Dashboard → Project Settings → API
   Gemini: https://aistudio.google.com/apikey */
window.SUPABASE_URL = 'https://YOUR_PROJECT.supabase.co';
window.SUPABASE_ANON_KEY = 'YOUR_SUPABASE_ANON_KEY';
window.GEMINI_API_KEY = 'YOUR_GEMINI_API_KEY';
/* Goods Program API (запуск: start-goods-program.bat) */
window.GOODS_PROGRAM_API_BASE = 'http://localhost:8000/api/v1/matching';
window.GOODS_PROGRAM_HEALTH_URL = 'http://localhost:8000/health';
/* true = no login screen (local building). false = require Firebase sign-in. */
window.DEV_SKIP_AUTH = true;
