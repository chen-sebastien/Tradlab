import {defineConfig} from '@playwright/test'
import path from 'node:path'
export default defineConfig({testDir:'./tests',timeout:90000,workers:1,retries:0,
 use:{baseURL:'http://127.0.0.1:8875',viewport:{width:1440,height:1000},screenshot:'only-on-failure',trace:'retain-on-failure'},
 webServer:{command:`"${path.resolve('../.venv/Scripts/python.exe')}" -m scripts.e2e_server`,cwd:path.resolve('..'),url:'http://127.0.0.1:8875/health',reuseExistingServer:false,timeout:60000},
 reporter:[['list'],['html',{open:'never'}]]})
