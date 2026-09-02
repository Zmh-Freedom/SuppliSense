import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import SupplierLibraryPage from '../components/SupplierLibraryPage';

const mocks = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock('../api', () => ({ api: mocks }));

function renderLibrary() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <SupplierLibraryPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('SupplierLibraryPage', () => {
  afterEach(() => vi.clearAllMocks());

  it('shows capability categories, products, regions, and current source', async () => {
    mocks.get.mockResolvedValue({
      items: [{
        _id: 'supplier-1',
        name: '示例汽车零部件有限公司',
        categories: ['汽车零部件'],
        products: ['制动卡钳、制动', '制动', '制动卡钳'],
        regions: ['华东'],
        status: 'active',
        source: 'feishu_bitable',
      }],
      total: 1,
    });

    renderLibrary();

    expect(await screen.findByText('示例汽车零部件有限公司')).toBeInTheDocument();
    expect(screen.getByText('汽车零部件')).toBeInTheDocument();
    expect(screen.getByText('华东')).toBeInTheDocument();
    expect(screen.getByText('供货产品：')).toBeInTheDocument();
    expect(screen.getByText('制动卡钳, 制动')).toBeInTheDocument();
    expect(screen.queryByText('制动卡钳、制动, 制动, 制动卡钳')).not.toBeInTheDocument();
    expect(screen.getByText('来源：飞书')).toBeInTheDocument();
    expect(screen.getByText(/已启用飞书同步时以飞书正式供应商数据为准/)).toBeInTheDocument();
  });
});
