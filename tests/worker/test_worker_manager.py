from queue import Full
from unittest.mock import Mock, patch

import pytest

from src.worker.manager import WorkerManager


class TestWorkerManager:
    """Test WorkerManager class."""

    def test_init_valid(self):
        """Test WorkerManager initialization with valid num_workers."""
        manager = WorkerManager(num_workers=4)
        assert manager.num_workers == 4
        assert not manager._running
        assert len(manager._processes) == 0

    def test_init_invalid_num_workers(self):
        """Test WorkerManager rejects invalid num_workers."""
        with pytest.raises(ValueError, match="num_workers must be at least 1"):
            WorkerManager(num_workers=0)

        with pytest.raises(ValueError, match="num_workers must be at least 1"):
            WorkerManager(num_workers=-1)

    @patch("src.worker.manager.logger")
    @patch("os.cpu_count", return_value=4)
    def test_init_warns_excessive_workers(self, mock_cpu_count, mock_logger):
        """Test warning when num_workers exceeds CPU count."""
        manager = WorkerManager(num_workers=8)
        assert manager.num_workers == 8
        mock_logger.warning.assert_called_once()
        assert "exceeds CPU count" in mock_logger.warning.call_args[0][0]

    def test_get_input_queues(self):
        """Test getting input queues creates them once."""
        manager = WorkerManager(num_workers=3)
        queues1 = manager.get_input_queues()
        queues2 = manager.get_input_queues()

        assert len(queues1) == 3
        assert queues1 is queues2  # Same instance

    def test_num_workers_property(self):
        """Test num_workers property."""
        manager = WorkerManager(num_workers=5)
        assert manager.num_workers == 5

    @patch("src.worker.manager.logger")
    def test_start_when_already_running(self, mock_logger):
        """Test start() when already running logs warning."""
        manager = WorkerManager(num_workers=1)
        manager._running = True

        manager.start()

        mock_logger.warning.assert_called_with("WorkerManager already running")

    @patch("src.worker.manager.logger")
    def test_stop_when_not_running(self, mock_logger):
        """Test stop() when not running logs warning."""
        manager = WorkerManager(num_workers=1)
        manager._running = False

        manager.stop()

        mock_logger.warning.assert_called_with("WorkerManager not running")

    def test_start_stop_lifecycle(self):
        """Test full start/stop lifecycle."""
        manager = WorkerManager(num_workers=2)

        # Start workers
        manager.start()
        assert manager._running
        assert len(manager._processes) == 2
        assert all(p.is_alive() for p in manager._processes)

        # Stop workers
        manager.stop(timeout=2.0)
        assert not manager._running
        assert len(manager._processes) == 0

    def test_is_healthy_when_running(self):
        """Test is_healthy returns True when all workers alive."""
        manager = WorkerManager(num_workers=2)
        manager.start()

        assert manager.is_healthy()

        manager.stop()

    def test_is_healthy_when_not_running(self):
        """Test is_healthy returns False when not running."""
        manager = WorkerManager(num_workers=2)
        assert not manager.is_healthy()

    def test_get_alive_count(self):
        """Test get_alive_count returns correct count."""
        manager = WorkerManager(num_workers=2)
        manager.start()

        assert manager.get_alive_count() == 2

        manager.stop()

    def test_get_stats_empty(self):
        """Test get_stats returns empty dict when no stats available."""
        manager = WorkerManager(num_workers=2)
        stats = manager.get_stats()
        assert stats == {}

    @patch("src.worker.manager.logger")
    def test_stop_with_full_queue(self, mock_logger):
        """Test stop() handles Full exception when sending sentinels."""
        manager = WorkerManager(num_workers=2)
        manager.start()

        # Mock queue to raise Full
        for queue in manager._input_queues:
            queue.put_nowait = Mock(side_effect=Full())

        manager.stop(timeout=1.0)

        # Should log warning about failed sentinel
        assert mock_logger.warning.call_count >= 2

    @patch("src.worker.manager.logger")
    def test_stop_force_terminate(self, mock_logger):
        """Test stop() force terminates hung workers."""
        manager = WorkerManager(num_workers=1)
        manager.start()

        # Mock process to not join gracefully
        mock_process = Mock()
        mock_process.is_alive.return_value = True
        mock_process.join = Mock()
        mock_process.terminate = Mock()
        mock_process.kill = Mock()
        mock_process.name = "test-worker"
        manager._processes = [mock_process]

        manager.stop(timeout=0.1)

        # Should attempt terminate
        mock_process.terminate.assert_called_once()
        mock_logger.warning.assert_called()

    @patch("src.worker.manager.logger")
    def test_stop_force_kill(self, mock_logger):
        """Test stop() kills workers that don't terminate."""
        manager = WorkerManager(num_workers=1)
        manager._running = True

        # Mock process that survives terminate
        mock_process = Mock()
        mock_process.is_alive.return_value = True
        mock_process.join = Mock()
        mock_process.terminate = Mock()
        mock_process.kill = Mock()
        mock_process.name = "test-worker"
        manager._processes = [mock_process]
        manager._input_queues = [Mock()]

        manager.stop(timeout=0.1)

        # Should attempt both terminate and kill
        mock_process.terminate.assert_called_once()
        mock_process.kill.assert_called_once()
        mock_logger.error.assert_called()
