import {mount} from '@vue/test-utils'
import {describe,it,expect} from 'vitest'
import LineChart from './LineChart.vue'
describe('可存取的資料圖表',()=>{
 it('空資料提供明確狀態，不產生假曲線',()=>{
  const wrapper=mount(LineChart,{props:{values:[]}})
  expect(wrapper.text()).toContain('尚無可顯示的曲線')
  expect(wrapper.find('svg').exists()).toBe(false)
 })
 it('相同數值及基準仍產生有限座標，顯示日期標籤',()=>{
  const wrapper=mount(LineChart,{props:{values:[100,100],secondary:[99,101],labels:['2024-01-02','2024-01-03']}})
  expect(wrapper.find('svg').attributes('role')).toBe('img')
  expect(wrapper.html()).not.toMatch(/NaN|Infinity/)
  expect(wrapper.text()).toContain('2024-01-02')
  expect(wrapper.findAll('path')).toHaveLength(3)
 })
})
