import { defineConfig } from 'vitepress'

export default defineConfig({
  title: 'C Cleaner Plus',
  description: 'Windows C 盘强力清理工具文档',
  base: '/c_cleaner_plus/',
  lastUpdated: true,
  cleanUrls: true,
  
  head: [
    ['link', { rel: 'icon', href: '/logo.png' }]
  ],

  themeConfig: {
    logo: '/logo.png',
    
    // 顶部导航栏
    nav: [
      { text: '首页', link: '/' },
      { text: '指南', link: '/guide/' },
      { text: '功能', link: '/features/' },
      { text: '配置', link: '/config/' },
      { text: 'FAQ', link: '/faq' },
    ],

    // 侧边栏
    sidebar: {
      '/guide/': [
        {
          text: '入门指南',
          items: [
            { text: '项目概述', link: '/guide/' },
            { text: '安装指南', link: '/guide/installation' },
            { text: '使用手册', link: '/guide/usage' },
          ]
        }
      ],
      '/features/': [
        {
          text: '功能特性',
          items: [
            { text: '常规清理', link: '/features/' },
            { text: '高级清理', link: '/features/advanced' },
            { text: '应用卸载', link: '/features/uninstall' },
          ]
        }
      ],
      '/config/': [
        {
          text: '配置说明',
          items: [
            { text: '配置文件', link: '/config/' },
          ]
        }
      ],
    },

    // 社交链接
    socialLinks: [
      { icon: 'github', link: 'https://github.com/Kiowx/c_cleaner_plus' }
    ],

    // 页脚
    footer: {
      message: '基于 MIT 许可证发布',
      copyright: 'Copyright © 2024-present Kiowx'
    },

    // 搜索配置
    search: {
      provider: 'local'
    },

    // 文档编辑链接
    editLink: {
      pattern: 'https://github.com/Kiowx/c_cleaner_plus/edit/main/docs/:path',
      text: '在 GitHub 上编辑此页面'
    },

    // 回到顶部
    outline: {
      label: '页面导航',
      level: 'deep'
    },

    // 上一页/下一页
    docFooter: {
      prev: '上一页',
      next: '下一页'
    }
  },

  // Markdown 配置
  markdown: {
    lineNumbers: true,
  },
})
