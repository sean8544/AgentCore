import { CloudSyncOutlined } from '@ant-design/icons';
import GoogleCard from '../../components/GoogleCard';
import GooglePageHeader from '../../components/GooglePageHeader';
import { useI18n } from '../../i18n';

export default function BackupsPage() {
  const { t } = useI18n();
  return (
    <div>
      <GooglePageHeader
        icon={<CloudSyncOutlined />}
        title={t('menu.backups')}
      />
      <GoogleCard>
        <div style={{ color: 'var(--google-muted-foreground)' }}>
          {t('common.comingSoon')}
        </div>
      </GoogleCard>
    </div>
  );
}
