import { createApp } from 'vue'
import { Quasar, Notify } from 'quasar'
import iconSet from 'quasar/icon-set/svg-material-icons'
import 'quasar/dist/quasar.css'
import './style.css'
import App from './App.vue'
createApp(App).use(Quasar,{iconSet,plugins:{Notify},config:{brand:{primary:'#21695b',secondary:'#c2df9b',negative:'#b7453b'}}}).mount('#app')
