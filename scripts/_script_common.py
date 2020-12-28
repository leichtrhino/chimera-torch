
import torch
from _training_common import StftSetting

def add_general_argument(parser):
    parser.add_argument('--gpu', action='store_true', help='enable cuda device')
    parser.add_argument('--segment-duration', type=float, help='segment duration in seconds')
    return parser

def validate_general_argument(args, parser):
    if args.gpu and not torch.cuda.is_available():
        parser.error(f'cuda is not available')
    # get prefered device
    args.device = torch.device('cuda' if args.gpu else 'cpu')
    if args.segment_duration is not None and args.segment_duration <= 0:
        parser.error('--segment-duration is positive')
    return args

def add_feature_argument(parser):
    parser.add_argument('--sr', type=int, default=8000, help='sampling rate')
    parser.add_argument('--n-fft', type=int, default=256, help='num of fft point')
    return parser

def validate_feature_argument(args, parser):
    if args.sr <= 0:
        parser.error('--sr is positive')
    if args.n_fft  <= 0:
        parser.error('--n-fft is positive')
    args.win_length = args.n_fft
    args.hop_length = args.win_length // 4;
    args.bin_num = args.n_fft // 2 + 1
    args.stft_setting = StftSetting(args.n_fft, args.hop_length, args.win_length)
    return args

def add_model_argument(parser):
    parser.add_argument('--n-hidden', type=int, default=600, help='num of hidden state')
    parser.add_argument('--embedding-dim', type=int, default=20, help='embedding dimension of deep clustering')
    parser.add_argument('--residual', action='store_true', help='residual base module')
    return parser

def validate_model_argument(args, parser):
    if args.n_hidden <= 0:
        parser.error('--n-hidden is positive')
    if args.embedding_dim <= 0:
        parser.error('--embedding-dim is positive')
    return args

