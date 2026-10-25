"""One candidate per short-lived process. Input bytes never touch disk."""
import io
import json
import os
import resource
import sys
import time

from models import CANDIDATES


def rss():
    with open('/proc/self/statm', encoding='ascii') as handle:
        return int(handle.read().split()[1]) * os.sysconf('SC_PAGE_SIZE')


def infer(candidate, image):
    import torch
    torch.set_num_threads(2)
    repo, revision = CANDIDATES[candidate]
    kwargs = dict(revision=revision, cache_dir='/data/model-cache')
    if candidate == 'mobilenet':
        import timm
        from huggingface_hub import hf_hub_download
        from torchvision import transforms as T
        checkpoint = torch.load(hf_hub_download(repo, 'best.pt', **kwargs),
                                map_location='cpu', weights_only=True)
        if checkpoint['model_name'] != 'mobilenetv4_conv_small':
            # Published checkpoint may use timm's pretrained configuration suffix.
            if checkpoint['model_name'] != 'mobilenetv4_conv_small.e2400_r224_in1k':
                raise ValueError('Unexpected architecture')
        labels = checkpoint['class_names']
        model = timm.create_model(checkpoint['model_name'], pretrained=False,
                                  num_classes=len(labels))
        model.load_state_dict(checkpoint['state_dict'])
        size = int(checkpoint['imgsz'])
        if not 1 <= size <= 1024:
            raise ValueError('Unexpected input size')
        prep = T.Compose([T.Resize((size, size)), T.ToTensor(),
                          T.Normalize(checkpoint['mean'], checkpoint['std'])])
        inputs = prep(image).unsqueeze(0)
        del checkpoint
        model.eval()
        start = time.monotonic()
        with torch.inference_mode():
            probs = model(inputs).softmax(1)[0].tolist()
    else:
        from transformers import AutoImageProcessor, SiglipForImageClassification
        processor = AutoImageProcessor.from_pretrained(repo, trust_remote_code=False, **kwargs)
        model = SiglipForImageClassification.from_pretrained(
            repo, use_safetensors=True, trust_remote_code=False, **kwargs).cpu().eval()
        inputs = processor(images=image, return_tensors='pt')
        labels = [model.config.id2label[i] for i in range(model.config.num_labels)]
        start = time.monotonic()
        with torch.inference_mode():
            probs = model(**inputs).logits.softmax(1)[0].tolist()
    return dict(rss_after_inference_bytes=rss(),
                peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
                probabilities=dict(zip(labels, probs)),
                inference_seconds=time.monotonic() - start)


if __name__ == '__main__':
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = 16_000_000
    before = rss()
    start = time.monotonic()
    data = sys.stdin.buffer.read(8 * 1024 * 1024 + 1)
    if len(data) > 8 * 1024 * 1024:
        raise ValueError('Image too large')
    with Image.open(io.BytesIO(data)) as source:
        with source.convert('RGB') as image:
            result = infer(sys.argv[1], image)
    result.update(rss_before_bytes=before, rss_after_bytes=rss(),
                  load_and_inference_seconds=time.monotonic() - start)
    print(json.dumps(result))
