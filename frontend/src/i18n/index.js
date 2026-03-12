import { createI18n } from 'vue-i18n'
import ru from '../locales/ru'
import en from '../locales/en'
import zh from '../locales/zh'

const i18n = createI18n({
  legacy: false,
  locale: import.meta.env.VITE_DEFAULT_LOCALE || 'ru',
  fallbackLocale: 'en',
  messages: {
    ru,
    en,
    zh
  }
})

export default i18n
