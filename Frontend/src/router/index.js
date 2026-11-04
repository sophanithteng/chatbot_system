import { createRouter, createWebHistory } from 'vue-router'
import ChatView from '../pages/ChatView.vue'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      name: 'chat',
      component: ChatView,
    },
  ],
})

export default router
