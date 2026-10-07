import {createApp} from 'vue'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import 'dayjs/locale/zh-cn'
import App from './App.vue'
import './style.css'
createApp(App).use(ElementPlus,{locale:zhCn}).mount('#app')
