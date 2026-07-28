import { StrictMode, type ReactNode } from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { RiskResult } from '../types'
import AssessView from '../components/AssessView'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
}))

vi.mock('../api', () => ({
  api: {
    get: mocks.get,
    post: mocks.post,
  },
}))

vi.mock('../hooks', () => ({
  useWatchlist: () => ({ companies: ['企业A'] }),
}))

vi.mock('../components/WatchlistPanel', () => ({
  default: ({ onSelect }: { onSelect: (company: string) => void }) => (
    <button onClick={() => onSelect('企业A')}>选择 URL 企业</button>
  ),
}))

vi.mock('../components/SentimentPanel', () => ({
  default: () => null,
}))

vi.mock('../components/Skeleton', () => ({
  default: () => null,
  SkeletonChart: () => null,
}))

vi.mock('recharts', () => {
  const Chart = ({ children }: { children?: ReactNode }) => <div>{children}</div>
  return {
    Area: Chart,
    CartesianGrid: Chart,
    Line: Chart,
    LineChart: Chart,
    ReferenceArea: Chart,
    ReferenceLine: Chart,
    ResponsiveContainer: Chart,
    Tooltip: Chart,
    XAxis: Chart,
    YAxis: Chart,
  }
})

const RISK_RESULT: RiskResult = {
  risk_score: 20,
  risk_level: '低风险',
  financial: null,
  risk_detail: {
    lawsuit_count: 0,
    executed_count: 0,
    dishonesty_count: 0,
    major_lawsuit: false,
    abnormal_operation_count: 0,
    administrative_penalty_count: 0,
    legal_person_change_frequent: false,
  },
  cached_at: null,
  cache_age_hours: null,
  is_stale: false,
  is_listed: false,
}

function renderAssess({ strict = false }: { strict?: boolean } = {}) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  const content = (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/assess/%E4%BC%81%E4%B8%9AA']}>
        <Routes>
          <Route path="/assess/:companyName?" element={<AssessView />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )

  return render(strict ? <StrictMode>{content}</StrictMode> : content)
}

describe('AssessView URL assessment', () => {
  beforeEach(() => {
    mocks.get.mockResolvedValue({ data: [] })
    mocks.post.mockResolvedValue(RISK_RESULT)
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('assesses an initial URL company only once in StrictMode', async () => {
    renderAssess({ strict: true })

    await waitFor(() => {
      expect(mocks.post).toHaveBeenCalledTimes(1)
    })
    expect(mocks.post).toHaveBeenCalledWith('/risk/assess', { company_name: '企业A' })
  })

  it('restores the URL company to the input before reassessing it', async () => {
    const user = userEvent.setup()
    renderAssess()

    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1))
    mocks.post.mockClear()

    const input = screen.getByPlaceholderText('输入企业名称搜索…')
    await user.clear(input)
    await user.type(input, '企业B')
    await user.click(screen.getByRole('button', { name: '选择 URL 企业' }))

    expect(input).toHaveValue('企业A')
    await waitFor(() => {
      expect(mocks.post).toHaveBeenCalledWith('/risk/assess', { company_name: '企业A' })
    })
  })
})
