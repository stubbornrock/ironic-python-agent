# Copyright 2011 Justin Santa Barbara
# Copyright 2012 Hewlett-Packard Development Company, L.P.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import errno
import glob
import os
import shutil
import tempfile
import testtools

import mock
from oslo_concurrency import processutils
from oslotest import base as test_base

from ironic_python_agent import errors
from ironic_python_agent import utils


class ExecuteTestCase(testtools.TestCase):
    """This class is a copy of the same class in openstack/ironic."""

    def test_retry_on_failure(self):
        fd, tmpfilename = tempfile.mkstemp()
        _, tmpfilename2 = tempfile.mkstemp()
        try:
            fp = os.fdopen(fd, 'w+')
            fp.write('''#!/bin/sh
# If stdin fails to get passed during one of the runs, make a note.
if ! grep -q foo
then
    echo 'failure' > "$1"
fi
# If stdin has failed to get passed during this or a previous run, exit early.
if grep failure "$1"
then
    exit 1
fi
runs="$(cat $1)"
if [ -z "$runs" ]
then
    runs=0
fi
runs=$(($runs + 1))
echo $runs > "$1"
exit 1
''')
            fp.close()
            os.chmod(tmpfilename, 0o755)
            try:
                self.assertRaises(processutils.ProcessExecutionError,
                                  utils.execute,
                                  tmpfilename, tmpfilename2, attempts=10,
                                  process_input=b'foo',
                                  delay_on_retry=False)
            except OSError as e:
                if e.errno == errno.EACCES:
                    self.skipTest("Permissions error detected. "
                                  "Are you running with a noexec /tmp?")
                else:
                    raise
            fp = open(tmpfilename2, 'r')
            runs = fp.read()
            fp.close()
            self.assertNotEqual(runs.strip(), 'failure',
                                'stdin did not always get passed correctly')
            runs = int(runs.strip())
            self.assertEqual(10, runs,
                             'Ran %d times instead of 10.' % (runs,))
        finally:
            os.unlink(tmpfilename)
            os.unlink(tmpfilename2)

    def test_unknown_kwargs_raises_error(self):
        self.assertRaises(processutils.UnknownArgumentError,
                          utils.execute,
                          '/usr/bin/env', 'true',
                          this_is_not_a_valid_kwarg=True)

    def test_check_exit_code_boolean(self):
        utils.execute('/usr/bin/env', 'false', check_exit_code=False)
        self.assertRaises(processutils.ProcessExecutionError,
                          utils.execute,
                          '/usr/bin/env', 'false', check_exit_code=True)

    def test_no_retry_on_success(self):
        fd, tmpfilename = tempfile.mkstemp()
        _, tmpfilename2 = tempfile.mkstemp()
        try:
            fp = os.fdopen(fd, 'w+')
            fp.write('''#!/bin/sh
# If we've already run, bail out.
grep -q foo "$1" && exit 1
# Mark that we've run before.
echo foo > "$1"
# Check that stdin gets passed correctly.
grep foo
''')
            fp.close()
            os.chmod(tmpfilename, 0o755)
            try:
                utils.execute(tmpfilename,
                              tmpfilename2,
                              process_input=b'foo',
                              attempts=2)
            except OSError as e:
                if e.errno == errno.EACCES:
                    self.skipTest("Permissions error detected. "
                                  "Are you running with a noexec /tmp?")
                else:
                    raise
        finally:
            os.unlink(tmpfilename)
            os.unlink(tmpfilename2)


class GetAgentParamsTestCase(test_base.BaseTestCase):

    @mock.patch('oslo_log.log.getLogger')
    @mock.patch('six.moves.builtins.open')
    def test__read_params_from_file_fail(self, logger_mock, open_mock):
        open_mock.side_effect = Exception
        params = utils._read_params_from_file('file-path')
        self.assertEqual({}, params)

    @mock.patch('six.moves.builtins.open')
    def test__read_params_from_file(self, open_mock):
        kernel_line = 'api-url=http://localhost:9999 baz foo=bar\n'
        open_mock.return_value.__enter__ = lambda s: s
        open_mock.return_value.__exit__ = mock.Mock()
        read_mock = open_mock.return_value.read
        read_mock.return_value = kernel_line
        params = utils._read_params_from_file('file-path')
        open_mock.assert_called_once_with('file-path')
        read_mock.assert_called_once_with()
        self.assertEqual('http://localhost:9999', params['api-url'])
        self.assertEqual('bar', params['foo'])
        self.assertFalse('baz' in params)

    @mock.patch.object(utils, '_set_cached_params')
    @mock.patch.object(utils, '_read_params_from_file')
    @mock.patch.object(utils, '_get_cached_params')
    def test_get_agent_params_kernel_cmdline(self, get_cache_mock,
                                             read_params_mock,
                                             set_cache_mock):
        get_cache_mock.return_value = {}
        expected_params = {'a': 'b'}
        read_params_mock.return_value = expected_params
        returned_params = utils.get_agent_params()
        read_params_mock.assert_called_once_with('/proc/cmdline')
        self.assertEqual(expected_params, returned_params)
        set_cache_mock.assert_called_once_with(expected_params)

    @mock.patch.object(utils, '_set_cached_params')
    @mock.patch.object(utils, '_get_vmedia_params')
    @mock.patch.object(utils, '_read_params_from_file')
    @mock.patch.object(utils, '_get_cached_params')
    def test_get_agent_params_vmedia(self, get_cache_mock,
                                     read_params_mock,
                                     get_vmedia_params_mock,
                                     set_cache_mock):
        get_cache_mock.return_value = {}
        kernel_params = {'boot_method': 'vmedia'}
        vmedia_params = {'a': 'b'}
        expected_params = dict(list(kernel_params.items()) +
                               list(vmedia_params.items()))
        read_params_mock.return_value = kernel_params
        get_vmedia_params_mock.return_value = vmedia_params

        returned_params = utils.get_agent_params()
        read_params_mock.assert_called_once_with('/proc/cmdline')
        self.assertEqual(expected_params, returned_params)
        # Make sure information is cached
        set_cache_mock.assert_called_once_with(expected_params)

    @mock.patch.object(utils, '_set_cached_params')
    @mock.patch.object(utils, '_get_cached_params')
    def test_get_agent_params_from_cache(self, get_cache_mock,
                                         set_cache_mock):
        get_cache_mock.return_value = {'a': 'b'}
        returned_params = utils.get_agent_params()
        expected_params = {'a': 'b'}
        self.assertEqual(expected_params, returned_params)
        self.assertEqual(0, set_cache_mock.call_count)

    @mock.patch('six.moves.builtins.open')
    @mock.patch.object(glob, 'glob')
    def test__get_vmedia_device(self, glob_mock, open_mock):

        glob_mock.return_value = ['/sys/class/block/sda/device/model',
                                  '/sys/class/block/sdb/device/model',
                                  '/sys/class/block/sdc/device/model']
        fobj_mock = mock.MagicMock()
        mock_file_handle = mock.MagicMock()
        mock_file_handle.__enter__.return_value = fobj_mock
        open_mock.return_value = mock_file_handle

        fobj_mock.read.side_effect = ['scsi disk', Exception, 'Virtual Media']
        vmedia_device_returned = utils._get_vmedia_device()
        self.assertEqual('sdc', vmedia_device_returned)

    @mock.patch.object(shutil, 'rmtree', autospec=True)
    @mock.patch.object(tempfile, 'mkdtemp', autospec=True)
    @mock.patch.object(utils, '_read_params_from_file')
    @mock.patch.object(os.path, 'exists')
    @mock.patch.object(os, 'mkdir')
    @mock.patch.object(utils, 'execute')
    def test__get_vmedia_params_by_label_lower_case(
            self, execute_mock, mkdir_mock, exists_mock, read_params_mock,
            mkdtemp_mock, rmtree_mock):
        mkdtemp_mock.return_value = "/tempdir"

        null_output = ["", ""]
        expected_params = {'a': 'b'}
        read_params_mock.return_value = expected_params
        exists_mock.side_effect = [True, False]
        execute_mock.side_effect = [null_output, null_output]

        returned_params = utils._get_vmedia_params()

        execute_mock.assert_any_call('mount', "/dev/disk/by-label/ir-vfd-dev",
                                     "/tempdir")
        read_params_mock.assert_called_once_with("/tempdir/parameters.txt")
        exists_mock.assert_called_once_with("/dev/disk/by-label/ir-vfd-dev")
        execute_mock.assert_any_call('umount', "/tempdir")
        self.assertEqual(expected_params, returned_params)
        mkdtemp_mock.assert_called_once_with()
        rmtree_mock.assert_called_once_with("/tempdir")

    @mock.patch.object(shutil, 'rmtree', autospec=True)
    @mock.patch.object(tempfile, 'mkdtemp', autospec=True)
    @mock.patch.object(utils, '_read_params_from_file')
    @mock.patch.object(os.path, 'exists')
    @mock.patch.object(os, 'mkdir')
    @mock.patch.object(utils, 'execute')
    def test__get_vmedia_params_by_label_upper_case(
            self, execute_mock, mkdir_mock, exists_mock, read_params_mock,
            mkdtemp_mock, rmtree_mock):
        mkdtemp_mock.return_value = "/tempdir"

        null_output = ["", ""]
        expected_params = {'a': 'b'}
        read_params_mock.return_value = expected_params
        exists_mock.side_effect = [False, True]
        execute_mock.side_effect = [null_output, null_output]

        returned_params = utils._get_vmedia_params()

        execute_mock.assert_any_call('mount', "/dev/disk/by-label/IR-VFD-DEV",
                                     "/tempdir")
        read_params_mock.assert_called_once_with("/tempdir/parameters.txt")
        exists_mock.assert_has_calls(
            [mock.call("/dev/disk/by-label/ir-vfd-dev"),
             mock.call("/dev/disk/by-label/IR-VFD-DEV")])
        execute_mock.assert_any_call('umount', "/tempdir")
        self.assertEqual(expected_params, returned_params)
        mkdtemp_mock.assert_called_once_with()
        rmtree_mock.assert_called_once_with("/tempdir")

    @mock.patch.object(shutil, 'rmtree', autospec=True)
    @mock.patch.object(tempfile, 'mkdtemp', autospec=True)
    @mock.patch.object(utils, '_get_vmedia_device')
    @mock.patch.object(utils, '_read_params_from_file')
    @mock.patch.object(os.path, 'exists')
    @mock.patch.object(os, 'mkdir')
    @mock.patch.object(utils, 'execute')
    def test__get_vmedia_params_by_device(self, execute_mock, mkdir_mock,
                                          exists_mock, read_params_mock,
                                          get_device_mock, mkdtemp_mock,
                                          rmtree_mock):
        mkdtemp_mock.return_value = "/tempdir"

        null_output = ["", ""]
        expected_params = {'a': 'b'}
        read_params_mock.return_value = expected_params
        exists_mock.side_effect = [False, False]
        execute_mock.side_effect = [null_output, null_output]
        get_device_mock.return_value = "sda"

        returned_params = utils._get_vmedia_params()

        exists_mock.assert_has_calls(
            [mock.call("/dev/disk/by-label/ir-vfd-dev"),
             mock.call("/dev/disk/by-label/IR-VFD-DEV")])
        execute_mock.assert_any_call('mount', "/dev/sda",
                                     "/tempdir")
        read_params_mock.assert_called_once_with("/tempdir/parameters.txt")
        execute_mock.assert_any_call('umount', "/tempdir")
        self.assertEqual(expected_params, returned_params)
        mkdtemp_mock.assert_called_once_with()
        rmtree_mock.assert_called_once_with("/tempdir")

    @mock.patch.object(utils, '_get_vmedia_device')
    @mock.patch.object(os.path, 'exists')
    def test__get_vmedia_params_cannot_find_dev(self, exists_mock,
                                                get_device_mock):
        get_device_mock.return_value = None
        exists_mock.return_value = False
        self.assertRaises(errors.VirtualMediaBootError,
                          utils._get_vmedia_params)

    @mock.patch.object(shutil, 'rmtree', autospec=True)
    @mock.patch.object(tempfile, 'mkdtemp', autospec=True)
    @mock.patch.object(utils, '_get_vmedia_device')
    @mock.patch.object(utils, '_read_params_from_file')
    @mock.patch.object(os.path, 'exists')
    @mock.patch.object(os, 'mkdir')
    @mock.patch.object(utils, 'execute')
    def test__get_vmedia_params_mount_fails(self, execute_mock,
                                            mkdir_mock, exists_mock,
                                            read_params_mock,
                                            get_device_mock, mkdtemp_mock,
                                            rmtree_mock):
        mkdtemp_mock.return_value = "/tempdir"

        expected_params = {'a': 'b'}
        exists_mock.return_value = True
        read_params_mock.return_value = expected_params
        get_device_mock.return_value = "sda"

        execute_mock.side_effect = processutils.ProcessExecutionError()

        self.assertRaises(errors.VirtualMediaBootError,
                          utils._get_vmedia_params)

        execute_mock.assert_any_call('mount', "/dev/disk/by-label/ir-vfd-dev",
                                     "/tempdir")
        mkdtemp_mock.assert_called_once_with()
        rmtree_mock.assert_called_once_with("/tempdir")

    @mock.patch.object(shutil, 'rmtree', autospec=True)
    @mock.patch.object(tempfile, 'mkdtemp', autospec=True)
    @mock.patch.object(utils, '_get_vmedia_device')
    @mock.patch.object(utils, '_read_params_from_file')
    @mock.patch.object(os.path, 'exists')
    @mock.patch.object(os, 'mkdir')
    @mock.patch.object(utils, 'execute')
    def test__get_vmedia_params_umount_fails(self, execute_mock, mkdir_mock,
                                             exists_mock, read_params_mock,
                                             get_device_mock, mkdtemp_mock,
                                             rmtree_mock):
        mkdtemp_mock.return_value = "/tempdir"

        null_output = ["", ""]
        expected_params = {'a': 'b'}
        exists_mock.return_value = True
        read_params_mock.return_value = expected_params
        get_device_mock.return_value = "sda"

        execute_mock.side_effect = [null_output,
                                    processutils.ProcessExecutionError()]

        returned_params = utils._get_vmedia_params()

        execute_mock.assert_any_call('mount', "/dev/disk/by-label/ir-vfd-dev",
                                     "/tempdir")
        read_params_mock.assert_called_once_with("/tempdir/parameters.txt")
        execute_mock.assert_any_call('umount', "/tempdir")
        self.assertEqual(expected_params, returned_params)
        mkdtemp_mock.assert_called_once_with()
        rmtree_mock.assert_called_once_with("/tempdir")

    @mock.patch.object(shutil, 'rmtree', autospec=True)
    @mock.patch.object(tempfile, 'mkdtemp', autospec=True)
    @mock.patch.object(utils, '_get_vmedia_device')
    @mock.patch.object(utils, '_read_params_from_file')
    @mock.patch.object(os.path, 'exists')
    @mock.patch.object(os, 'mkdir')
    @mock.patch.object(utils, 'execute')
    def test__get_vmedia_params_rmtree_fails(self, execute_mock, mkdir_mock,
                                             exists_mock, read_params_mock,
                                             get_device_mock, mkdtemp_mock,
                                             rmtree_mock):
        mkdtemp_mock.return_value = "/tempdir"
        rmtree_mock.side_effect = Exception

        null_output = ["", ""]
        expected_params = {'a': 'b'}
        exists_mock.return_value = True
        read_params_mock.return_value = expected_params
        get_device_mock.return_value = "sda"

        execute_mock.return_value = null_output

        returned_params = utils._get_vmedia_params()

        execute_mock.assert_any_call('mount', "/dev/disk/by-label/ir-vfd-dev",
                                     "/tempdir")
        read_params_mock.assert_called_once_with("/tempdir/parameters.txt")
        execute_mock.assert_any_call('umount', "/tempdir")
        self.assertEqual(expected_params, returned_params)
        mkdtemp_mock.assert_called_once_with()
        rmtree_mock.assert_called_once_with("/tempdir")

    @mock.patch.object(utils, 'get_agent_params')
    def test_parse_root_device_hints(self, mock_get_params):
        mock_get_params.return_value = {
            'root_device': 'vendor=SpongeBob,model=Square%20Pants',
            'ipa-api-url': 'http://1.2.3.4:1234'
        }
        expected = {'vendor': 'spongebob', 'model': 'square pants'}
        result = utils.parse_root_device_hints()
        self.assertEqual(expected, result)

    @mock.patch.object(utils, 'get_agent_params')
    def test_parse_root_device_hints_wwn(self, mock_get_params):
        mock_get_params.return_value = {
            'root_device': 'wwn=abcd,wwn_vendor_extension=ext,'
            'wwn_with_extension=abcd_ext',
            'ipa-api-url': 'http://1.2.3.4:1234'
        }
        expected = {'wwn': 'abcd', 'wwn_vendor_extension': 'ext',
                    'wwn_with_extension': 'abcd_ext'}
        result = utils.parse_root_device_hints()
        self.assertEqual(expected, result)

    @mock.patch.object(utils, 'get_agent_params')
    def test_parse_root_device_hints_no_hints(self, mock_get_params):
        mock_get_params.return_value = {
            'ipa-api-url': 'http://1.2.3.4:1234'
        }
        result = utils.parse_root_device_hints()
        self.assertEqual({}, result)

    @mock.patch.object(utils, 'get_agent_params')
    def test_parse_root_device_size(self, mock_get_params):
        mock_get_params.return_value = {
            'root_device': 'size=12345',
            'ipa-api-url': 'http://1.2.3.4:1234'
        }
        result = utils.parse_root_device_hints()
        self.assertEqual(12345, result['size'])

    @mock.patch.object(utils, 'get_agent_params')
    def test_parse_root_device_not_supported(self, mock_get_params):
        mock_get_params.return_value = {
            'root_device': 'foo=bar,size=12345',
            'ipa-api-url': 'http://1.2.3.4:1234'
        }
        self.assertRaises(errors.DeviceNotFound,
                          utils.parse_root_device_hints)


class TestFailures(testtools.TestCase):
    def test_get_error(self):
        f = utils.AccumulatedFailures()
        self.assertFalse(f)
        self.assertIsNone(f.get_error())

        f.add('foo')
        f.add('%s', 'bar')
        f.add(RuntimeError('baz'))
        self.assertTrue(f)

        exp = ('The following errors were encountered:\n* foo\n* bar\n* baz')
        self.assertEqual(exp, f.get_error())

    def test_raise(self):
        class FakeException(Exception):
            pass

        f = utils.AccumulatedFailures(exc_class=FakeException)
        self.assertIsNone(f.raise_if_needed())
        f.add('foo')
        self.assertRaisesRegexp(FakeException, 'foo', f.raise_if_needed)
