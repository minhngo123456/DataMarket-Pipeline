import { CloudUploadOutlined, InboxOutlined } from '@ant-design/icons';
import { Alert, Button, Card, Col, Descriptions, Progress, Row, Select, Space, Table, Tag, Upload, message } from 'antd';
import axios from 'axios';
import { useEffect, useRef, useState } from 'react';
import { PageHeading } from '../../components/PageHeading.jsx';
import { apiErrorMessage, useApiData } from '../../hooks/useApiData.js';
import { mockSources } from '../../mocks/data.js';
import { marketDataApi } from '../../services/marketDataApi.js';
import { REQUIRED_COLUMNS, parseCsvPreview, validateCsvFile } from './csvPreview.js';

const { Dragger } = Upload;
const POLL_MS = 3000;
const POLL_LIMIT = 40;
const JOB_STATUS = {
  queued: ['blue', 'Đang chờ'],
  extracting: ['processing', 'Đang đọc tệp'],
  normalizing: ['processing', 'Đang chuẩn hóa'],
  completed: ['green', 'Hoàn tất'],
};

export function UploadPage() {
  const sources = useApiData(() => marketDataApi.getSources(), mockSources);
  const csvSources = (sources.data?.data || []).filter((s) => s.kind === 'csv' && s.enabled !== false);
  const [sourceId, setSourceId] = useState(null);
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [progress, setProgress] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [job, setJob] = useState(null);
  const polls = useRef(0);

  useEffect(() => {
    if (!job || job.status === 'completed' || sources.isMock) return undefined;
    if (polls.current >= POLL_LIMIT) return undefined;
    const timer = setTimeout(async () => {
      polls.current += 1;
      try {
        setJob(await marketDataApi.getUploadJobStatus(job.job_id));
      } catch {
        // Keep the last known status; the user can check Catalog later.
      }
    }, POLL_MS);
    return () => clearTimeout(timer);
  }, [job, sources.isMock]);

  const reset = () => {
    setFile(null);
    setPreview(null);
    setProgress(0);
  };

  const selectFile = async (chosen) => {
    const problem = validateCsvFile(chosen);
    if (problem) {
      message.error(problem);
      return Upload.LIST_IGNORE;
    }
    try {
      setPreview(await parseCsvPreview(chosen));
      setFile(chosen);
      setJob(null);
    } catch {
      message.error('Không đọc được tệp CSV.');
    }
    return false; // never let antd upload by itself
  };

  const upload = async () => {
    setUploading(true);
    setProgress(5);
    try {
      if (sources.isMock) {
        await new Promise((resolve) => setTimeout(resolve, 800));
        setProgress(100);
        setJob({ job_id: 'job-sample', status: 'completed', row_count: preview.totalRows, counts: { accepted: preview.totalRows - 1, pending: 1, rejected: 0 } });
      } else {
        const created = await marketDataApi.createUploadJob({ source_id: sourceId, filename: file.name });
        setProgress(20);
        // Presigned S3 URL: plain axios so the Cognito token is not sent to S3.
        await axios.put(created.upload_url, file, {
          headers: { 'Content-Type': 'text/csv' },
          onUploadProgress: (event) => event.total && setProgress(20 + Math.round((event.loaded / event.total) * 80)),
        });
        setProgress(100);
        polls.current = 0;
        setJob({ job_id: created.job_id, status: 'queued', row_count: 0, counts: { accepted: 0, pending: 0, rejected: 0 } });
      }
      message.success(`Đã tải lên ${file.name}. Hệ thống đang xử lý.`);
      reset();
    } catch (error) {
      message.error(`Tải lên thất bại: ${apiErrorMessage(error)}`);
      setProgress(0);
    } finally {
      setUploading(false);
    }
  };

  const [statusColor, statusLabel] = JOB_STATUS[job?.status] || ['default', job?.status];
  const columns = (preview?.fields || []).map((field) => ({ title: field, dataIndex: field, key: field, ellipsis: true }));

  return (
    <>
      <PageHeading eyebrow="NHẬP DỮ LIỆU" title="Tải CSV giá" description="Tải bảng giá CSV cho một nguồn. Tệp đi thẳng lên S3 rồi được chuẩn hóa tự động." />
      {sources.isMock && <Alert className="page-alert" type="warning" showIcon title="Chưa kết nối được API: tải lên chỉ được mô phỏng, không có tệp nào được gửi đi." />}
      <Row gutter={[20, 20]}>
        <Col xs={24} xl={16}>
          <Card className="panel-card upload-card" variant="borderless">
            <div className="form-section-heading"><span className="step-index">1</span><div><h2>Chọn nguồn CSV</h2><p>Chỉ nguồn loại CSV đang bật mới nhận tệp tải lên.</p></div></div>
            <Select className="full-select" size="large" placeholder="Chọn nguồn dữ liệu" value={sourceId} onChange={setSourceId} loading={sources.loading}
              options={csvSources.map((s) => ({ value: s.source_id, label: s.name }))} notFoundContent="Chưa có nguồn CSV nào đang bật" />

            <div className="form-section-heading upload-step-two"><span className="step-index">2</span><div><h2>Chọn tệp</h2><p>Chỉ nhận .csv, tối đa 10 MB, mã hóa UTF-8.</p></div></div>
            <Dragger accept=".csv,text/csv" multiple={false} showUploadList={false} beforeUpload={selectFile} disabled={uploading}>
              <p className="upload-cloud"><InboxOutlined /></p>
              <p><strong>{file ? file.name : 'Kéo thả tệp CSV vào đây hoặc bấm để chọn'}</strong></p>
              <p className="muted">{file ? `${(file.size / 1024).toFixed(0)} KB · ${preview?.totalRows ?? 0} dòng` : '.csv · tối đa 10 MB'}</p>
            </Dragger>

            {preview && (
              <div className="csv-preview">
                {preview.missing.length > 0 && <Alert type="error" showIcon title={`Thiếu cột bắt buộc: ${preview.missing.join(', ')}`} />}
                <p className="muted">Xem trước 5 dòng đầu:</p>
                <Table size="small" bordered columns={columns} dataSource={preview.rows.map((row, i) => ({ ...row, _key: i }))} rowKey="_key" pagination={false} scroll={{ x: true }} />
              </div>
            )}
            {progress > 0 && <Progress percent={progress} />}
            <div className="upload-footer">
              <Space>
                <Button type="primary" icon={<CloudUploadOutlined />} loading={uploading} disabled={!file || !sourceId || preview?.missing.length > 0} onClick={upload}>Tải lên</Button>
                {file && <Button onClick={reset} disabled={uploading}>Hủy</Button>}
              </Space>
            </div>
          </Card>
        </Col>
        <Col xs={24} xl={8}>
          {job && (
            <Card className="panel-card" variant="borderless" title="Tiến trình xử lý">
              <Descriptions column={1} size="small">
                <Descriptions.Item label="Mã job">{job.job_id}</Descriptions.Item>
                <Descriptions.Item label="Trạng thái"><Tag color={statusColor}>{statusLabel}</Tag></Descriptions.Item>
                <Descriptions.Item label="Số dòng">{job.row_count}</Descriptions.Item>
                <Descriptions.Item label="Đã nhận">{job.counts?.accepted ?? 0}</Descriptions.Item>
                <Descriptions.Item label="Chờ duyệt SKU">{job.counts?.pending ?? 0}</Descriptions.Item>
                <Descriptions.Item label="Bị loại">{job.counts?.rejected ?? 0}</Descriptions.Item>
              </Descriptions>
            </Card>
          )}
          <Card className="panel-card help-card" variant="borderless">
            <h2>Định dạng tệp</h2>
            {REQUIRED_COLUMNS.map((column) => <div className="format-row" key={column}><span>{column}</span><span className="format-required">Bắt buộc</span></div>)}
            <div className="format-row"><span>price_basis</span><span>Nên có: tax_included</span></div>
            <p className="muted">Thiếu <code>price_basis</code> thì giá vẫn được lưu nhưng không vào ma trận (ma trận so sánh giá đã gồm thuế).</p>
            <p className="muted">Ví dụ: <code>001-PHONE,20100000,VND,2026-10-19T00:45:00Z,tax_included</code></p>
          </Card>
        </Col>
      </Row>
    </>
  );
}
