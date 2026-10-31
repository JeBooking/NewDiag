import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'
import '@vue-flow/minimap/dist/style.css'
import './styles/index.scss'
import App from './App.vue'
import router from './router'
import { debugLog } from './services/api'

const app = createApp(App)

app.config.errorHandler = (error, _instance, info) => {
  console.error('[newdiag] 组件错误', info, error)
}

window.addEventListener('unhandledrejection', (event) => {
  console.error('[newdiag] 未处理的 Promise 拒绝', event.reason)
})

debugLog('应用启动', { dev: import.meta.env.DEV, href: location.href })

app.use(createPinia()).use(router).use(ElementPlus, { locale: zhCn }).mount('#app')
